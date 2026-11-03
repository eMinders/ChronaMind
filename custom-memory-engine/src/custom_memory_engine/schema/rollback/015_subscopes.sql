-- Rollback of migration 15 (sub-scopes, per-scope exclusion key and indexes). Run in ONE transaction by whoever
-- operates the schema; the forward runner never executes it. Restores the version-14 schema exactly (the catalog
-- fingerprint equals the one recorded for version 14). Roll back later migrations first.
--
-- It REFUSES if any row lies in a sub-scope, because version 14 cannot represent one: the restored
-- `<table>_scope_matches_user` CHECK requires the personal root. Silently flattening a sub-scope row into its owner's
-- root would merge two scopes' beliefs into one, which is a data change, not a rollback. Move or delete those rows
-- first.
-- The watermark and the exclusion key are re-keyed back to `user_id`, which is lossless: every surviving row is in
-- its personal root, so `scope_path` determines `user_id` uniquely.

DO $guard$
DECLARE
  tbl text;
  personal text[] := ARRAY[
    'memory_version', 'memory_evidence', 'memory_derivation', 'memory_embedding', 'memory_conflict',
    'turn', 'session', 'job', 'audit_event', 'deletion_request',
    'association', 'abstraction_stats', 'verification'
  ];
  offenders int := 0;
  found int;
BEGIN
  FOREACH tbl IN ARRAY personal LOOP
    EXECUTE pg_catalog.format(
      'SELECT count(*) FROM %I WHERE scope_path <> ''/u/'' || user_id || ''/''', tbl
    ) INTO found;
    offenders := offenders + found;
  END LOOP;
  IF offenders > 0 THEN
    RAISE EXCEPTION 'sub_scope_rows_present_rollback_refused' USING ERRCODE = 'P0001';
  END IF;
END
$guard$;

-- 1. The facet index returns to the user key.
DROP INDEX memory_version_um_facet_idx;
CREATE INDEX memory_version_um_facet_idx ON memory_version (user_id, um_facet) WHERE um_facet IS NOT NULL;

-- 2. The watermark returns to the user key.
DROP TRIGGER trg_memory_version_recorded_watermark ON memory_version;
DELETE FROM clock_watermark;
ALTER TABLE clock_watermark RENAME COLUMN scope_path TO user_id;
CREATE OR REPLACE FUNCTION enforce_recorded_watermark() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  instant timestamptz;
BEGIN
  -- NULL (staged), empty and unbounded-below ranges are rejected by the CHECK constraints; they put nothing on the timeline.
  IF NEW.recorded_range IS NULL OR isempty(NEW.recorded_range) OR lower_inf(NEW.recorded_range) THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.recorded_range IS NOT DISTINCT FROM OLD.recorded_range THEN
    RETURN NEW;
  END IF;
  -- The instant this write puts on the belief timeline: a belief start, or the moment a belief is closed.
  instant := GREATEST(lower(NEW.recorded_range), CASE WHEN upper_inf(NEW.recorded_range) THEN NULL ELSE upper(NEW.recorded_range) END);
  INSERT INTO clock_watermark AS w (user_id, high) VALUES (NEW.user_id, instant)
  ON CONFLICT (user_id) DO UPDATE SET high = GREATEST(w.high, EXCLUDED.high)
  WHERE EXCLUDED.high >= w.high
  RETURNING w.high INTO instant;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'recorded_time_moved_backwards' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER trg_memory_version_recorded_watermark
  BEFORE INSERT OR UPDATE ON memory_version
  FOR EACH ROW
  EXECUTE FUNCTION enforce_recorded_watermark();
INSERT INTO clock_watermark (user_id, high)
SELECT user_id,
       max(GREATEST(lower(recorded_range), CASE WHEN upper_inf(recorded_range) THEN NULL ELSE upper(recorded_range) END))
  FROM memory_version
 WHERE recorded_range IS NOT NULL
 GROUP BY user_id;

-- 3. The exclusion key returns to the user key.
--    Version 14's exclusion index carries a stale attribute name: migration 9 renamed the COLUMN `valid_range` to
--    `event_range` in place, and a table rename does not rename the index's own attributes. Recreating the
--    constraint on `event_range` would leave an `event_range` attribute where version 14 has `valid_range`, which
--    the catalog fingerprint sees. Creating it under the legacy name and then renaming the column back reproduces
--    version 14 exactly (verified against a freshly built version 14).
ALTER TABLE memory_version DROP CONSTRAINT no_bitemporal_overlap;
ALTER TABLE memory_version RENAME COLUMN event_range TO valid_range;
ALTER TABLE memory_version ADD CONSTRAINT no_bitemporal_overlap EXCLUDE USING gist (
  user_id WITH =,
  subject_key WITH =,
  predicate_key WITH =,
  valid_range WITH &&,
  recorded_range WITH &&
) WHERE (single_valued AND status IN ('active', 'archived'));
ALTER TABLE memory_version RENAME COLUMN valid_range TO event_range;

-- 4. The personal-root equality returns.
DO $m15$
DECLARE
  tbl text;
  personal text[] := ARRAY[
    'memory_version', 'memory_evidence', 'memory_derivation', 'memory_embedding', 'memory_conflict',
    'turn', 'session', 'job', 'audit_event', 'deletion_request',
    'association', 'abstraction_stats', 'verification'
  ];
BEGIN
  FOREACH tbl IN ARRAY personal LOOP
    EXECUTE pg_catalog.format('ALTER TABLE %I DROP CONSTRAINT %I', tbl, tbl || '_scope_within_user');
    EXECUTE pg_catalog.format(
      'ALTER TABLE %I ADD CONSTRAINT %I CHECK (scope_path = ''/u/'' || user_id || ''/'')',
      tbl, tbl || '_scope_matches_user'
    );
  END LOOP;
END
$m15$;

-- 5. The function is pinned again, as migration 9 pinned it.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.enforce_recorded_watermark() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;

DELETE FROM _cme_schema_migrations WHERE version = 15;
