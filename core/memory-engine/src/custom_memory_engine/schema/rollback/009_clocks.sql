-- Rollback of migration 9 (M3, E-012). Run in ONE transaction by whoever operates the schema; the forward runner never
-- executes it. Restores the version-8 schema exactly (the catalog fingerprint equals the one recorded for version 8).
-- Dropping the columns discards observed_at / event_basis, and dropping clock_watermark discards the watermark; both are
-- derivable again by re-applying (D8 backfill, and the maximum recorded instant per user).
-- After a re-apply the two new columns sit at later attribute numbers (PostgreSQL never reuses a dropped one); every
-- other catalog property is identical. Roll back later migrations first.

DROP TRIGGER IF EXISTS trg_memory_version_recorded_watermark ON memory_version;
DROP TRIGGER IF EXISTS trg_memory_version_clocks_dual_write ON memory_version;
DROP FUNCTION IF EXISTS enforce_recorded_watermark();
DROP FUNCTION IF EXISTS sync_memory_version_clocks();
DROP TABLE clock_watermark;
ALTER TABLE memory_version DROP CONSTRAINT memory_version_event_basis_check;
ALTER TABLE memory_version DROP COLUMN observed_at, DROP COLUMN event_basis;

ALTER TABLE memory_version RENAME COLUMN event_range TO valid_range;

DO $m3$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
  definition text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['guard_update()', 'enforce_archival_invariant()', 'guard_derivation_coverage()'] LOOP
    definition := pg_catalog.pg_get_functiondef(
      pg_catalog.to_regprocedure(pg_catalog.format('%I.%s', engine_schema, fn))
    );
    EXECUTE pg_catalog.replace(definition, 'event_range', 'valid_range');
  END LOOP;
END
$m3$;
DELETE FROM _cme_schema_migrations WHERE version = 9;
