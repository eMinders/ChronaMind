-- Rollback of migration 23: language-agnostic lexical indexing and multilingual dense slots (W3.5).
-- Run in ONE transaction; restores the version-22 schema. Roll back later migrations first.
--
-- DATA LOSS, stated rather than discovered: dropping the four new slot columns discards every vector stored under a
-- multilingual model. The legacy `embedding vector(384)` column is untouched (only its NOT NULL returns), so a v1
-- deployment loses nothing it had before migration 23. Re-embedding is a backfill, and nothing is fabricated on
-- reapply.
--
-- `pg_trgm` is NOT dropped. Extension ownership belongs to the deployment's bootstrap (`tests/init_extensions.sql`
-- documents the requirement), not to a schema migration: dropping an extension another schema or another engine may
-- have began using would be a rollback that damages something it never created.

DROP INDEX IF EXISTS memory_embedding_3072_hnsw_idx;
DROP INDEX IF EXISTS memory_embedding_1536_hnsw_idx;
DROP INDEX IF EXISTS memory_embedding_1024_hnsw_idx;
DROP INDEX IF EXISTS memory_embedding_768_hnsw_idx;

ALTER TABLE memory_embedding
  DROP CONSTRAINT IF EXISTS memory_embedding_slot_dimensions_check,
  DROP CONSTRAINT IF EXISTS memory_embedding_exactly_one_vector_check,
  DROP COLUMN IF EXISTS embedding_3072,
  DROP COLUMN IF EXISTS embedding_1536,
  DROP COLUMN IF EXISTS embedding_1024,
  DROP COLUMN IF EXISTS embedding_768;

-- A row written under a multilingual model has no legacy vector, so it cannot survive the NOT NULL that version 22
-- had. Removing it is the only coherent downgrade: version 22 has nowhere to put a 1024-wide vector, and keeping the
-- row with a NULL vector would leave a memory that claims to be embedded and is not. The *memory* is untouched -- its
-- `memory_version` row, evidence and provenance all stay; only the derived vector goes, and re-embedding restores it.
DELETE FROM memory_embedding WHERE embedding IS NULL;
ALTER TABLE memory_embedding ALTER COLUMN embedding SET NOT NULL;

DROP INDEX IF EXISTS memory_version_content_trgm_idx;
DROP INDEX IF EXISTS memory_version_content_fts_simple_idx;
ALTER TABLE memory_version DROP COLUMN IF EXISTS content_fts_simple;

DELETE FROM _cme_schema_migrations WHERE version = 23;
