-- Rollback of migration 31: verification artifact pinning.
-- Run in ONE transaction; restores the version-30 schema. Roll back later migrations first.
--
-- Refuses while any verification_artifact rows exist unless:
--   SET LOCAL cme.rollback_discard = 'verification_artifacts';

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM verification_artifact)
     AND COALESCE(pg_catalog.current_setting('cme.rollback_discard', true), '') <> 'verification_artifacts' THEN
    RAISE EXCEPTION 'm31_rollback_refused_verification_artifacts_exist' USING ERRCODE = 'P0001';
  END IF;
END
$$;

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
