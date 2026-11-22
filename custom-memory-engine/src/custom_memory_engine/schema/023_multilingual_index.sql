-- Migration 23: language-agnostic lexical indexing and multilingual dense-vector slots (W3.5, V2-D86).
--
-- The storage substrate built at W1 was correct but English-locked in two places, and both are defects rather than
-- preferences:
--
-- 1. `memory_version.content_fts` is `to_tsvector('english', content)`. English stemming is not merely useless for
--    Arabic, Cyrillic or CJK text -- it *discards* the morphology, so `plainto_tsquery('english', ...)` matches noise.
--    A language-agnostic lexical arm needs a tokeniser with no language, and `'simple'` is exactly that: it lowercases
--    and splits, and nothing else. `pg_trgm` supplies the substring/fuzzy arm that survives transliteration, spelling
--    variation and mixed-script identifiers (`bge-m3`, `text-embedding-3`).
-- 2. `memory_embedding.embedding` is `vector(384)` because the only model the v1 baseline shipped was
--    `BAAI/bge-small-en-v1.5`. That model cannot embed Arabic or CJK semantics usefully, and a 384-wide column makes
--    every multilingual embedder (`bge-m3` 1024d, `multilingual-e5-large` 1024d, `text-embedding-3-large` 3072d) a
--    schema change. The column is therefore widened into **dimension slots**: one nullable column per supported
--    dimension class, each with its own HNSW index, with a CHECK that a row fills exactly one.
--
-- Why slots rather than one "configurable" column. pgvector's HNSW index is built for one fixed dimension, and a
-- `vector` column without a typmod cannot be indexed at all. A single table therefore cannot hold arbitrary dimensions
-- *and* be ANN-indexed. Slots give exactly the property that matters: **coexistence during a re-embedding rollout**
-- (the old model's vectors and the new model's vectors live side by side, each searchable), without a second database,
-- without a per-deployment migration parameter, and without an approximate index over a column whose dimension the
-- planner cannot know. Adding a dimension class later is a one-paragraph migration (`ADD COLUMN embedding_<n>
-- halfvec(<n>)`, HNSW index, add it to the CHECK); that is the documented extension point, and it is cheaper than
-- pretending the schema is dimension-agnostic when it is not.
--
-- Supported classes, chosen because a real embedder exists for each:
--   384   `BAAI/bge-small-en-v1.5` (the v1 baseline; kept byte-identical on the legacy column)
--   768   `nomic-embed-text`, `gemini-embedding-001` (768 output)
--   1024  `BAAI/bge-m3`, `intfloat/multilingual-e5-large`, `Qwen/Qwen3-Embedding-0.6B`
--   1536  `text-embedding-3-small`, `gemini-embedding-001` (1536 output)
--   3072  `text-embedding-3-large`, `gemini-embedding-001` (3072 output)
--
-- `halfvec` (IEEE-754 binary16) rather than `vector` for the new slots: pgvector's own benchmarks put recall loss at
-- well under a point for cosine search while halving storage and index size, and HNSW on `halfvec` supports up to
-- 4000 dimensions, which `vector` does not.
--
-- Nothing here is destructive. `embedding` keeps its type and its data; only its NOT NULL is lifted so a 1024d row can
-- exist in the same table. The legacy `content_fts` column and its index stay: v1 retrieval reads them, and a reader
-- that wants English stemming may still ask for it. The new arms are additive.
--
-- Requires `pg_trgm`. `vector` and `btree_gist` are already installed by migration 1; `halfvec` needs no extension of
-- its own.

-- `SCHEMA public`, explicitly. The runner already bootstraps every extension in `public` before it creates the
-- managed schema (`migrate.EXTENSIONS`), and `IF NOT EXISTS` makes this a no-op there. It is spelled out so the
-- migration is also correct when the DDL is applied directly -- by a test fixture, a replay harness or an operator --
-- because without the qualifier the extension would install into the *managed* schema (it is first on `search_path`),
-- where `reset`'s `DROP SCHEMA ... RESTRICT` refuses to run and the catalog fingerprint would begin carrying another
-- project's functions.
CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public;

-- 1. Language-agnostic lexical surface ---------------------------------------------------------------------------
--
-- `'simple'` is the only PostgreSQL text-search configuration with no language: it lowercases per the database
-- collation and splits on non-word characters. It is the correct tokeniser for a corpus whose language is per-row and
-- unknown to the database. Trigram matching covers what a tokeniser cannot: Arabic root variation without diacritics,
-- transliterated names, partially-typed queries and mixed-script identifiers.
ALTER TABLE memory_version
  ADD COLUMN content_fts_simple tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED;

CREATE INDEX memory_version_content_fts_simple_idx ON memory_version USING gin (content_fts_simple);

-- `gin_trgm_ops` on the column itself. Trigram GIN indexes are lossy for `%` similarity but exact for `ILIKE` and
-- substring predicates, which is the arm that rescues a query whose tokens the `'simple'` tokeniser split differently
-- from the document (agglutinative Arabic, German compounds, hyphenated identifiers).
CREATE INDEX memory_version_content_trgm_idx ON memory_version USING gin (content gin_trgm_ops);

-- 2. Multilingual dense-vector slots -----------------------------------------------------------------------------
--
-- A row fills exactly one slot: a version may carry several models (the PK is `(version_id, embedding_model)`), and
-- during a rollout the *same* version legitimately carries both a 384d and a 1024d vector. What may not happen is a
-- row that fills two slots -- that is two vectors in one row, which the PK already prevents -- or none.
ALTER TABLE memory_embedding ALTER COLUMN embedding DROP NOT NULL;

ALTER TABLE memory_embedding
  ADD COLUMN embedding_768 halfvec(768),
  ADD COLUMN embedding_1024 halfvec(1024),
  ADD COLUMN embedding_1536 halfvec(1536),
  ADD COLUMN embedding_3072 halfvec(3072);

ALTER TABLE memory_embedding
  ADD CONSTRAINT memory_embedding_exactly_one_vector_check CHECK (
    (embedding IS NOT NULL)::int
    + (embedding_768 IS NOT NULL)::int
    + (embedding_1024 IS NOT NULL)::int
    + (embedding_1536 IS NOT NULL)::int
    + (embedding_3072 IS NOT NULL)::int
    = 1
  ),
  -- The slot is the dimension. A 1024-wide vector in the 1536 slot would be silently unsearchable by the arm that
  -- owns that slot, so it is refused at the row rather than discovered as a missing hit.
  ADD CONSTRAINT memory_embedding_slot_dimensions_check CHECK (
    (embedding IS NULL OR vector_dims(embedding) = 384)
    AND (embedding_768 IS NULL OR vector_dims(embedding_768) = 768)
    AND (embedding_1024 IS NULL OR vector_dims(embedding_1024) = 1024)
    AND (embedding_1536 IS NULL OR vector_dims(embedding_1536) = 1536)
    AND (embedding_3072 IS NULL OR vector_dims(embedding_3072) = 3072)
  );

-- One HNSW index per slot, partial on the slot being filled: an index over an all-NULL column indexes nothing and
-- costs nothing, so a deployment that only ever uses 1024 pays for one index. Cosine, not L2: every embedder this
-- engine supports is trained for cosine similarity, and pgvector's `<=>` on normalised and unnormalised vectors alike
-- orders identically to cosine distance.
CREATE INDEX memory_embedding_768_hnsw_idx ON memory_embedding
  USING hnsw (embedding_768 halfvec_cosine_ops) WHERE embedding_768 IS NOT NULL;
CREATE INDEX memory_embedding_1024_hnsw_idx ON memory_embedding
  USING hnsw (embedding_1024 halfvec_cosine_ops) WHERE embedding_1024 IS NOT NULL;
CREATE INDEX memory_embedding_1536_hnsw_idx ON memory_embedding
  USING hnsw (embedding_1536 halfvec_cosine_ops) WHERE embedding_1536 IS NOT NULL;
CREATE INDEX memory_embedding_3072_hnsw_idx ON memory_embedding
  USING hnsw (embedding_3072 halfvec_cosine_ops) WHERE embedding_3072 IS NOT NULL;

-- The legacy 384 slot keeps exact search on purpose: it is what the v1 evaluations were measured against, and an
-- approximate index would move a published benchmark's numbers under a migration that claims to be additive.

-- 3. Postconditions ---------------------------------------------------------------------------------------------
DO $verify$
DECLARE
  missing text;
BEGIN
  SELECT string_agg(name, ', ') INTO missing
  FROM (VALUES
    ('memory_version_content_fts_simple_idx'),
    ('memory_version_content_trgm_idx'),
    ('memory_embedding_768_hnsw_idx'),
    ('memory_embedding_1024_hnsw_idx'),
    ('memory_embedding_1536_hnsw_idx'),
    ('memory_embedding_3072_hnsw_idx')
  ) AS wanted(name)
  WHERE pg_catalog.to_regclass(name) IS NULL;

  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'migration_023_postcondition_failed: missing index(es) %', missing;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
    WHERE c.relname = 'memory_version' AND a.attname = 'content_fts_simple' AND a.attgenerated = 's'
  ) THEN
    RAISE EXCEPTION 'migration_023_postcondition_failed: content_fts_simple is not a stored generated column';
  END IF;
END
$verify$;
