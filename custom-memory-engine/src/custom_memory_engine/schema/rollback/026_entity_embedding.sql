-- Rollback of migration 26: the entity embedding slot.
-- Run in ONE transaction; restores the version-25 schema. Roll back later migrations first.
--
-- DATA LOSS, stated rather than discovered: every entity vector and its model id are dropped, and they are not
-- recomputable from anything in the schema -- the model would have to be run again over each entity's canonical name.
-- The resolver falls back to exact, alias and trigram, which is exactly the behaviour before this migration, so the
-- downgrade is safe to run; it is only lossy.
DROP INDEX IF EXISTS entity_embedding_1024_hnsw_idx;
ALTER TABLE entity
  DROP CONSTRAINT IF EXISTS entity_embedding_model_paired,
  DROP COLUMN IF EXISTS embedding_model,
  DROP COLUMN IF EXISTS embedding_1024;

DELETE FROM _cme_schema_migrations WHERE version = 26;
