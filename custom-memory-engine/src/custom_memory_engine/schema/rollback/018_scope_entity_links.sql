-- Rollback of migration 18 (scope-local entity links, E-024).
-- Run in ONE transaction by whoever operates the schema; the forward runner never executes it.
-- Restores the version-17 schema exactly (the catalog fingerprint equals the one recorded for version 17).
-- Roll back later migrations first.
--
-- REFUSES if any entity_link rows exist: removing them would destroy cross-scope identity links.
-- Remove those rows first.

DO $guard$
DECLARE
  link_count int;
BEGIN
  SELECT count(*) INTO link_count FROM entity_link;
  IF link_count > 0 THEN
    RAISE EXCEPTION 'entity_link_rows_present_rollback_refused' USING ERRCODE = 'P0001';
  END IF;
END
$guard$;

DROP TABLE IF EXISTS entity_link;

DELETE FROM _cme_schema_migrations WHERE version = 18;
