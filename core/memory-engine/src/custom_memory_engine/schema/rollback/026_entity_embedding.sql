-- Rollback of migration 26: the entity embedding slot.
-- Run in ONE transaction; restores the version-25 schema. Roll back later migrations first.
--
-- It REFUSES while any entity carries a vector (`m26_rollback_refused_entity_vectors_exist`), like rollbacks 8 and 11:
-- the vectors and their model ids are not recomputable from the schema -- the model would have to run again over each
-- entity's name. The resolver's behaviour after a downgrade is exactly the pre-26 cascade, so the loss is the only
-- cost, and the operator acknowledges it by name in the same transaction:
--
--   SET LOCAL cme.rollback_discard = 'entity_embedding';
-- This rollback's data guard must see every tenant's rows. Under FORCE ROW LEVEL SECURITY a role that cannot bypass it (an
-- ordinary owner included) sees none, so the guard would pass on an empty view and the rollback would drop data it
-- was written to protect. Such a role is refused first (`V2-D123`).
DO $bypass$
BEGIN
  IF NOT (SELECT rolsuper OR rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
    RAISE EXCEPTION 'rollback_requires_bypassrls' USING ERRCODE = '42501';
  END IF;
END
$bypass$;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM entity WHERE embedding_1024 IS NOT NULL)
     AND COALESCE(pg_catalog.current_setting('cme.rollback_discard', true), '') <> 'entity_embedding' THEN
    RAISE EXCEPTION 'm26_rollback_refused_entity_vectors_exist' USING ERRCODE = 'P0001';
  END IF;
END
$$;

DROP INDEX IF EXISTS entity_embedding_1024_hnsw_idx;
ALTER TABLE entity
  DROP CONSTRAINT IF EXISTS entity_embedding_model_paired,
  DROP COLUMN IF EXISTS embedding_model,
  DROP COLUMN IF EXISTS embedding_1024;

DELETE FROM _cme_schema_migrations WHERE version = 26;
