-- Rollback of migration 10 (M6 + the E-016 record half). Run in ONE transaction by whoever operates the schema; the
-- forward runner never executes it. Restores the version-9 schema exactly (the catalog fingerprint equals the one
-- recorded for version 9). Dropping the tables discards associations, entities, ledgers, verifications, negative
-- constraints and the registered methods; dropping memory_version.method_id discards the method references.
-- After a re-apply the new column sits at a later attribute number (PostgreSQL never reuses a dropped one); every other
-- catalog property is identical. Roll back later migrations first.

DROP TRIGGER IF EXISTS trg_memory_version_transition_records ON memory_version;
DROP TRIGGER IF EXISTS trg_memory_version_zero_ledger ON memory_version;
DROP TRIGGER IF EXISTS trg_memory_version_method_reference ON memory_version;
DROP FUNCTION IF EXISTS enforce_transition_records();
DROP FUNCTION IF EXISTS create_zero_ledger();
DROP FUNCTION IF EXISTS assign_memory_version_method();

-- The pre-migration-10 bodies, verbatim from 005_axes.sql and 007_lifecycle_guard.sql.
CREATE OR REPLACE FUNCTION enforce_provenance() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_status text;
  current_lifecycle text;
  current_layer text;
BEGIN
  IF NEW.status IN ('active', 'archived') THEN
    IF NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'active_or_archived_version_requires_evidence_or_derivation'
        USING ERRCODE = 'P0001';
    END IF;
  END IF;
  -- A deferred trigger sees the row as it was when the event was queued, but the row can change before commit (a
  -- derived claim is inserted `asserted` and becomes `inferred` when its derivation row arrives). Judge what is
  -- committed: re-read the current row, and stop if it is gone.
  SELECT status, lifecycle, layer INTO current_status, current_lifecycle, current_layer
    FROM memory_version WHERE id = NEW.id;
  IF FOUND AND current_status = 'active' THEN
    IF current_lifecycle IN ('observed', 'asserted')
       AND NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id) THEN
      RAISE EXCEPTION 'lifecycle_requires_evidence_record' USING ERRCODE = 'P0001';
    END IF;
    -- I1': inferred => a derivation row (the method half arrives with E-014). v1 `persona` rows are the one inferred
    -- shape v1 can write without a derivation (they carry evidence), so they are exempt for the dual-write window.
    IF current_lifecycle = 'inferred' AND current_layer <> 'persona'
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'inferred_requires_derivation_record' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION mark_derived_version_inferred() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE memory_version
     SET lifecycle = 'inferred'
   WHERE id = NEW.derived_version_id
     AND status = 'active'
     AND kind = 'claim'
     AND lifecycle = 'asserted';
  RETURN NULL;
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['enforce_provenance()', 'mark_derived_version_inferred()'] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;

DROP TABLE negative_constraint;
DROP TABLE association;
DROP TABLE entity;
DROP TABLE verification;
DROP TABLE abstraction_stats;
ALTER TABLE memory_version DROP COLUMN method_id;
DROP TABLE method;
DROP FUNCTION IF EXISTS guard_negative_constraint();
DROP FUNCTION IF EXISTS guard_verification_no_delete();
DROP FUNCTION IF EXISTS guard_verification_append_only();
DROP FUNCTION IF EXISTS guard_abstraction_stats();
DROP FUNCTION IF EXISTS guard_method_append_only();
DELETE FROM _cme_schema_migrations WHERE version = 10;
