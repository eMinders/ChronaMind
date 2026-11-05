-- Migration 18: persisted child scopes and scope-local entity links (E-024 substrate).
--
-- Scope paths remain principal-owned. Child paths are admitted only below their user's root,
-- and the API verifies that each path is backed by a persisted scope before writing to it.

CREATE TABLE scope (
  scope_path text PRIMARY KEY,
  user_id text NOT NULL REFERENCES app_user(id),
  parent_path text,
  segment text NOT NULL CHECK (segment ~ '^[a-z0-9][a-z0-9-]*$'),
  kind text NOT NULL CHECK (kind IN ('project', 'session', 'task', 'shared')),
  created_at timestamptz NOT NULL DEFAULT clock_now(),
  CONSTRAINT scope_user_path_key UNIQUE (user_id, scope_path),
  CONSTRAINT scope_path_matches_parent CHECK (
    (parent_path IS NULL AND scope_path = '/u/' || user_id || '/' || segment || '/')
    OR
    (parent_path IS NOT NULL AND scope_path = parent_path || segment || '/'
      AND pg_catalog.starts_with(parent_path, '/u/' || user_id || '/'))
  ),
  CONSTRAINT scope_parent_same_user_fk FOREIGN KEY (user_id, parent_path)
    REFERENCES scope (user_id, scope_path)
);

CREATE INDEX scope_parent_path_idx ON scope (user_id, parent_path);
ALTER TABLE scope ENABLE ROW LEVEL SECURITY;
ALTER TABLE scope FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_read ON scope FOR SELECT
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));
CREATE POLICY scope_insert ON scope FOR INSERT
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));

CREATE TABLE entity_link (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES app_user(id),
  entity_a text NOT NULL,
  entity_b text NOT NULL,
  relation text NOT NULL CHECK (relation ~ '^[a-z][a-z0-9_]{0,63}$'),
  created_at timestamptz NOT NULL DEFAULT clock_now(),
  CONSTRAINT entity_link_ordered_entities CHECK (entity_a < entity_b),
  CONSTRAINT entity_link_unique_relation UNIQUE (user_id, entity_a, entity_b, relation),
  CONSTRAINT entity_link_a_owner_fk FOREIGN KEY (entity_a, user_id)
    REFERENCES entity (id, user_id) ON DELETE CASCADE,
  CONSTRAINT entity_link_b_owner_fk FOREIGN KEY (entity_b, user_id)
    REFERENCES entity (id, user_id) ON DELETE CASCADE
);

ALTER TABLE entity_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_link FORCE ROW LEVEL SECURITY;
CREATE POLICY entity_link_read ON entity_link FOR SELECT
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));
CREATE POLICY entity_link_insert ON entity_link FOR INSERT
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));

-- A version's repeated key retains its original scope when internal operations create another version.
-- Dependent records inherit the scope of their owning version instead of silently falling back to root.
CREATE OR REPLACE FUNCTION fill_scope_path() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  row_data jsonb := pg_catalog.to_jsonb(NEW);
  parent_id text;
  inherited_scope text;
BEGIN
  IF NEW.scope_path IS NULL THEN
    IF TG_TABLE_NAME = 'memory_version' THEN
      SELECT scope_path INTO inherited_scope
        FROM memory_version
       WHERE user_id = NEW.user_id AND memory_key = row_data ->> 'memory_key'
       ORDER BY id
       LIMIT 1;
    ELSE
      parent_id := CASE TG_TABLE_NAME
        WHEN 'memory_evidence' THEN row_data ->> 'version_id'
        WHEN 'memory_embedding' THEN row_data ->> 'version_id'
        WHEN 'memory_conflict' THEN row_data ->> 'candidate_version_id'
        WHEN 'memory_derivation' THEN row_data ->> 'derived_version_id'
        WHEN 'association' THEN row_data ->> 'from_id'
        WHEN 'abstraction_stats' THEN row_data ->> 'artifact_id'
        WHEN 'verification' THEN row_data ->> 'artifact_id'
        WHEN 'audit_event' THEN row_data ->> 'memory_ref'
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

DO $scope$
DECLARE
  tbl text;
  scoped_tables text[] := ARRAY[
    'memory_version', 'memory_evidence', 'memory_derivation', 'memory_embedding', 'memory_conflict',
    'turn', 'session', 'job', 'audit_event', 'deletion_request',
    'association', 'abstraction_stats', 'verification', 'entity', 'negative_constraint'
  ];
BEGIN
  FOREACH tbl IN ARRAY scoped_tables LOOP
    EXECUTE pg_catalog.format(
      'ALTER TABLE %I ADD CONSTRAINT %I CHECK (pg_catalog.starts_with(scope_path, ''/u/'' || user_id || ''/''))',
      tbl, tbl || '_scope_matches_user'
    );
  END LOOP;
END
$scope$;

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
