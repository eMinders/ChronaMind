-- Rollback of migration 29: artifact_provenance.
-- Run in ONE transaction; restores the version-28 schema. Roll back later migrations first.
--
-- Refuses while any non-pre-migration provenance rows exist unless the operator sets:
--   SET LOCAL cme.rollback_discard = 'artifact_provenance';
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
  IF EXISTS (SELECT 1 FROM artifact_provenance WHERE origin_kind <> 'pre_migration')
     AND COALESCE(pg_catalog.current_setting('cme.rollback_discard', true), '') <> 'artifact_provenance' THEN
    RAISE EXCEPTION 'm29_rollback_refused_artifact_provenance_exists' USING ERRCODE = 'P0001';
  END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_entity_link_cascade_provenance ON entity_link;
DROP TRIGGER IF EXISTS trg_entity_cascade_provenance ON entity;
DROP TRIGGER IF EXISTS trg_association_cascade_provenance ON association;
DROP TRIGGER IF EXISTS trg_memory_version_mark_artifact_provenance_deleted ON memory_version;
DROP TRIGGER IF EXISTS trg_entity_link_provenance ON entity_link;
DROP TRIGGER IF EXISTS trg_entity_provenance ON entity;
DROP TRIGGER IF EXISTS trg_association_provenance ON association;

DROP FUNCTION IF EXISTS cascade_artifact_provenance_delete();
DROP FUNCTION IF EXISTS mark_artifact_provenance_source_deleted();
DROP FUNCTION IF EXISTS enforce_artifact_provenance();

DROP POLICY IF EXISTS artifact_provenance_isolation ON artifact_provenance;
DROP TABLE IF EXISTS artifact_provenance;

DELETE FROM _cme_schema_migrations WHERE version = 29;
