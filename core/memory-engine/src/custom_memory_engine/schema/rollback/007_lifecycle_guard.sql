-- Rollback of migration 7 (E-016). Run in ONE transaction by whoever operates the schema; the forward runner never
-- executes it. Restores the version-6 schema exactly (the catalog fingerprint equals the one recorded for version 5).
-- Roll back later migrations first.

DROP TRIGGER IF EXISTS trg_memory_version_lifecycle_guard ON memory_version;
DROP FUNCTION IF EXISTS enforce_lifecycle_transition();

-- The pre-migration-6 body, verbatim from 001_full_schema.sql.
CREATE OR REPLACE FUNCTION enforce_provenance() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.status IN ('active', 'archived') THEN
    IF NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'active_or_archived_version_requires_evidence_or_derivation'
        USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

-- V2-D54 (S-02): engine functions are pinned to `<this schema>, pg_catalog, pg_temp`. CREATE OR REPLACE resets a
-- function's settings, so enforce_provenance's pin of migration 4 is restored.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['enforce_provenance()'] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;

ALTER TABLE memory_version DROP CONSTRAINT memory_version_lifecycle_legal_for_kind;
DELETE FROM _cme_schema_migrations WHERE version = 7;
