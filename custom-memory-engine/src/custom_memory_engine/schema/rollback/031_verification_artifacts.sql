-- Rollback of migration 31: verification artifact pinning.
-- Run in ONE transaction; restores the version-30 schema. Roll back later migrations first.
--
-- Refuses while any verification_artifact rows exist unless:
--   SET LOCAL cme.rollback_discard = 'verification_artifacts';
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
  IF EXISTS (SELECT 1 FROM verification_artifact)
     AND COALESCE(pg_catalog.current_setting('cme.rollback_discard', true), '') <> 'verification_artifacts' THEN
    RAISE EXCEPTION 'm31_rollback_refused_verification_artifacts_exist' USING ERRCODE = 'P0001';
  END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_verification_pinning_status_is_not_caller_settable ON verification;
DROP FUNCTION IF EXISTS guard_verification_pinning_status();
DROP TRIGGER IF EXISTS trg_verification_requires_artifacts ON verification;
DROP TRIGGER IF EXISTS trg_verification_artifact_no_truncate ON verification_artifact;
DROP TRIGGER IF EXISTS trg_verification_artifact_append_only ON verification_artifact;

DROP FUNCTION IF EXISTS enforce_verification_artifacts();
DROP FUNCTION IF EXISTS guard_verification_artifact_append_only();

DROP POLICY IF EXISTS verification_artifact_scope_read ON verification_artifact;
DROP POLICY IF EXISTS verification_artifact_isolation ON verification_artifact;
DROP TABLE IF EXISTS verification_artifact;

ALTER TABLE verification
  DROP CONSTRAINT IF EXISTS verification_pinning_status_check,
  DROP COLUMN IF EXISTS artifact_pinning_status,
  DROP CONSTRAINT IF EXISTS verification_id_user_unique;

DELETE FROM _cme_schema_migrations WHERE version = 31;
