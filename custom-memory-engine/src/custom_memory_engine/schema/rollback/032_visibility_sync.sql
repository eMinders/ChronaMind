-- Rollback of migration 32: owner-controlled visibility sync.
-- Run in ONE transaction; restores the version-31 schema. Roll back later migrations first.

DROP FUNCTION IF EXISTS set_artifact_visibility(text, text, text);

CREATE OR REPLACE FUNCTION guard_verification_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'verification_is_append_only' USING ERRCODE = 'P0001';
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_verification_append_only() SET search_path = %I, pg_catalog, pg_temp',
    engine_schema, engine_schema
  );
END
$pin$;

DELETE FROM _cme_schema_migrations WHERE version = 32;
