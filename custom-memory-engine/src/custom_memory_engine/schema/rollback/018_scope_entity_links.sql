-- Rollback of migration 18 (persisted child scopes and entity links, E-024).
-- Run in ONE transaction by whoever operates the schema; the forward runner never executes it.
-- Restores the version-17 schema exactly (the catalog fingerprint equals the one recorded for version 17).
-- Roll back later migrations first.
--
-- REFUSES if any entity_link or scope rows exist: removing them would destroy cross-scope identity links.
-- Remove those rows first.

DO $guard$
DECLARE
  link_count int;
  scope_count int;
BEGIN
  SELECT count(*) INTO link_count FROM entity_link;
  SELECT count(*) INTO scope_count FROM scope;
  IF link_count > 0 OR scope_count > 0 THEN
    RAISE EXCEPTION 'entity_link_or_scope_rows_present_rollback_refused' USING ERRCODE = 'P0001';
  END IF;
END
$guard$;

-- 1. Remove the fill_scope_path trigger modifications and revert to the migration-11 version.
--    Migration 18 replaced fill_scope_path() to support entity and negative_constraint tables.
--    Restore the simpler version that only handles the core scoped tables.
CREATE OR REPLACE FUNCTION fill_scope_path() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parent_id text;
  inherited_scope text;
BEGIN
  IF NEW.scope_path IS NULL THEN
    IF TG_TABLE_NAME = 'memory_version' THEN
      SELECT scope_path INTO inherited_scope
        FROM memory_version
       WHERE user_id = NEW.user_id AND memory_key = NEW.memory_key
       ORDER BY id
       LIMIT 1;
    ELSE
      parent_id := CASE TG_TABLE_NAME
        WHEN 'memory_evidence' THEN NEW.version_id
        WHEN 'memory_embedding' THEN NEW.version_id
        WHEN 'memory_conflict' THEN NEW.candidate_version_id
        WHEN 'memory_derivation' THEN NEW.derived_version_id
        WHEN 'association' THEN NEW.from_id
        WHEN 'abstraction_stats' THEN NEW.artifact_id
        WHEN 'verification' THEN NEW.artifact_id
        ELSE NULL
      END;
      IF parent_id IS NOT NULL THEN
        SELECT scope_path INTO inherited_scope
          FROM memory_version
         WHERE id = parent_id AND user_id = NEW.user_id;
      END IF;
    END IF;
    NEW.scope_path := COALESCE(inherited_scope, '/u/' || NEW.user_id || '/');
  END IF;
  RETURN NEW;
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.fill_scope_path() SET search_path = %I, pg_catalog, pg_temp',
    engine_schema, engine_schema
  );
END
$pin$;

-- 2. Revert the scope constraint changes on entity and negative_constraint (remove _scope_matches_user additions).
--    Migration 18 added scope_matches_user to entity and negative_constraint.
DO $revert$
DECLARE
  tbl text;
BEGIN
  FOR tbl IN VALUES ('entity'), ('negative_constraint') LOOP
    IF EXISTS (
      SELECT 1 FROM pg_catalog.pg_constraint
      WHERE connamespace = current_schema()::regnamespace AND conname = tbl || '_scope_matches_user'
    ) THEN
      EXECUTE pg_catalog.format('ALTER TABLE %I DROP CONSTRAINT %I', tbl, tbl || '_scope_matches_user');
    END IF;
  END LOOP;
END
$revert$;

-- 3. Drop the entity_link table (cascade removes its triggers and indexes).
DROP TABLE IF EXISTS entity_link;

-- 4. Drop the scope table (cascade removes its triggers and indexes).
DROP TABLE IF EXISTS scope;

DELETE FROM _cme_schema_migrations WHERE version = 18;
