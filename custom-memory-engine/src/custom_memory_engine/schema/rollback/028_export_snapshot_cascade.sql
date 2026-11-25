-- Rollback of migration 28: restore migration 25's guard, which refused every DELETE including the cascade.
-- Run in ONE transaction; restores the version-27 schema. Roll back later migrations first.
--
-- Lossless for data. The behavioural consequence of downgrading is worth stating: with the old guard, a user who has
-- ever exported cannot be deleted at all.
CREATE OR REPLACE FUNCTION guard_export_snapshot_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'export_snapshot_is_append_only' USING ERRCODE = 'P0001';
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_export_snapshot_append_only() SET search_path = %I, pg_catalog, pg_temp',
    engine_schema, engine_schema
  );
END
$pin$;

DELETE FROM _cme_schema_migrations WHERE version = 28;
