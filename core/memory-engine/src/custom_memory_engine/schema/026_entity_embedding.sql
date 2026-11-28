-- Migration 26: an embedding slot on `entity`, so the vector stage of entity resolution can exist (V2-D101).
--
-- `semantic/entities.py` documented a four-stage cascade -- exact, alias, trigram, vector -- and the vector stage was
-- dead code: it queried `entity.embedding`, a column migration 10 never created. The earlier fix removed the query and
-- recorded that restoring the stage needs a migration. This is that migration.
--
-- Why it earns its place, measured (`reports/entity_resolution/README.md`): a cross-script or transliterated mention
-- scores 0.50-0.79 against its entity and 0.35-0.49 against every other one, and the trigram stage scores it at ~0.0
-- because "ليلى حسن" shares no trigram with "Layla Hassan". That is the one case the deterministic stages cannot serve.
--
-- What this migration deliberately does **not** enable, and the measurement behind it: a vector score is *not* evidence
-- for a merge. Two different entities of the same shape are nearly identical in embedding space -- "Munich office" scores
-- 0.769 against "Berlin office", and "Project Beta" scores 0.791 against "Project Alpha" -- so a merge at that threshold
-- would attach one office's (or project's) claims to another, permanently, through a typed foreign key. The stage
-- therefore only ever *proposes*; a merge still needs an exact match or an adjudicated confirmation
-- (`semantic/entities.resolve_mention`).
--
-- Additive: one nullable column, one generated model-identity column, one index. No row is rewritten and no existing
-- column changes type. A row without a vector keeps resolving through exact, alias and trigram exactly as before.

ALTER TABLE entity
  ADD COLUMN embedding_1024 halfvec(1024),
  -- The model that produced the vector, so a re-embedding rollout can coexist and a stale vector is identifiable rather
  -- than silently compared against a new model's query vectors. NULL means "no vector", never "unknown model".
  ADD COLUMN embedding_model text,
  ADD CONSTRAINT entity_embedding_model_paired CHECK (
    (embedding_1024 IS NULL AND embedding_model IS NULL)
    OR (embedding_1024 IS NOT NULL AND embedding_model IS NOT NULL AND btrim(embedding_model) <> '')
  );

-- HNSW over the same cosine distance the claim index uses. `halfvec` because the slot is 1024 wide and the claim side
-- already stores that width as `halfvec` (migration 23); one precision for both keeps the two comparisons identical.
CREATE INDEX entity_embedding_1024_hnsw_idx ON entity
  USING hnsw (embedding_1024 halfvec_cosine_ops) WITH (m = 16, ef_construction = 64);

-- V2-D54 (S-02): no function is added, so nothing to pin. The column is written by `create_entity` only, under the
-- caller's own row-level security, and `guard_update` does not cover `entity` -- which is why the resolver treats a
-- vector as a *proposal* rather than as a durable fact about identity.
