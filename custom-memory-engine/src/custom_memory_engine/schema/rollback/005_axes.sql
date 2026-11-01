-- Rollback of migration 5 (M1 + M7, E-010). Run in ONE transaction by whoever operates the schema; the forward
-- runner never executes this file. Restores the pre-M1 schema exactly: the catalog fingerprint afterwards equals the
-- one recorded for version 4 (tests/integration/test_migration_m1.py proves it, and that re-applying works).
--
-- Safe while `layer` is stored (V2-D49 D6). `layer` is never rewritten by M1, so nothing else needs restoring; once a
-- later change drops `layer`, this rollback can no longer restore `semantic` versus `opinion`.
-- PostgreSQL never reuses a dropped attribute number, so after a re-apply the three columns sit at positions 23-25
-- instead of 20-22; every other catalog property is identical.
-- Rolling back M1 requires that no later migration (006+) is applied: roll those back first.

DROP TRIGGER IF EXISTS trg_memory_derivation_mark_inferred ON memory_derivation;
DROP TRIGGER IF EXISTS trg_memory_version_axes_dual_write ON memory_version;
DROP FUNCTION IF EXISTS mark_derived_version_inferred();
DROP FUNCTION IF EXISTS sync_memory_version_axes();

-- The pre-M1 body, verbatim from 001_full_schema.sql.
CREATE OR REPLACE FUNCTION guard_update() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  -- Belief content is immutable in place. The only permitted in-place mutations are
  -- status, archival_branch, the upper bound of recorded_range, and an increment of
  -- reinforcement_count (Technical Specification §5: a duplicate reinforces the
  -- existing version instead of inserting a new one).
  IF (
       NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.predicate_key, NEW.single_valued,
       NEW.content, NEW.valid_range, NEW.layer, NEW.valid_from_basis, NEW.assertion_mode,
       NEW.confidence, NEW.importance, NEW.extractor_version
     ) IS DISTINCT FROM (
       OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.predicate_key, OLD.single_valued,
       OLD.content, OLD.valid_range, OLD.layer, OLD.valid_from_basis, OLD.assertion_mode,
       OLD.confidence, OLD.importance, OLD.extractor_version
     )
  THEN
    RAISE EXCEPTION 'only status, archival_branch, reinforcement_count and the upper bound of recorded_range may change';
  END IF;
  IF NOT (NEW.reinforcement_count > OLD.reinforcement_count) THEN
    IF NEW.reinforcement_count IS DISTINCT FROM OLD.reinforcement_count THEN
      RAISE EXCEPTION 'reinforcement_count may only increase';
    END IF;
  END IF;
  IF NEW.archival_branch IS DISTINCT FROM OLD.archival_branch THEN
    IF NOT (OLD.archival_branch IS NULL AND NEW.archival_branch IN ('S', 'C') AND NEW.status = 'archived') THEN
      RAISE EXCEPTION 'archival_branch may only be set by an archive transition (status = archived)';
    END IF;
  END IF;
  IF NEW.recorded_range IS DISTINCT FROM OLD.recorded_range THEN
    IF OLD.recorded_range IS NULL AND NEW.status = 'active' AND upper_inf(NEW.recorded_range)
       AND lower(NEW.recorded_range) = clock_now() THEN
      NULL;
    ELSIF OLD.recorded_range IS NOT NULL AND upper_inf(OLD.recorded_range)
       AND lower(NEW.recorded_range) = lower(OLD.recorded_range)
       AND NOT upper_inf(NEW.recorded_range)
       AND upper(NEW.recorded_range) = clock_now() THEN
      NULL;
    ELSE
      RAISE EXCEPTION 'illegal recorded_range change';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

-- CREATE OR REPLACE reset the function's settings; restore the V2-D54 pin of migration 4.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['guard_update()'] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;

ALTER TABLE memory_version
  DROP CONSTRAINT memory_version_kind_check,
  DROP CONSTRAINT memory_version_lifecycle_check,
  DROP CONSTRAINT memory_version_retention_check;
ALTER TABLE memory_version DROP COLUMN kind, DROP COLUMN lifecycle, DROP COLUMN retention;
DELETE FROM _cme_schema_migrations WHERE version = 5;
