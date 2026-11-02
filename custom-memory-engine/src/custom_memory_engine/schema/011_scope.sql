-- Migration 11: scope hierarchy and path-based row-level security (M2 + M9) - E-011 (#62), V2-D05, V2-D35, V2-D36, V2-D45,
-- V2-D49 D11/D12/D13, V2-D54.
--
-- Every scoped row gets a `scope_path` (the canonical trailing-separator path of MA-011) and a `visibility`
-- (private | project | shared). The path is backfilled to the owner's personal root, `/u/<user_id>/`, and `user_id`
-- STAYS stored (D11: a generated user_id would break every v1 INSERT that names it, which rules out a dual-write window).
-- While every scope is personal a CHECK ties the two together; a later decision that admits sub-scopes replaces it.
--
-- READ policy (V2-D45): `<table>_scope_read` lets a caller read (a) every row in its own namespace, and (b) `project` /
-- `shared` rows that lie inside a scope an ACTIVE `scope_grant` gives it - never `private` rows through a grant. It
-- reads the caller only from `app.user_id` and the grants only from `scope_grant`. Matching is `starts_with`, never LIKE
-- (V2-D35): `/u/4/` does not match `/u/42/`, and `_` is not a wildcard. The expression is the SQL text of
-- `scope.access.rls_read_using`; a test pins the two together. The row's columns are qualified with the table name, which is
-- mandatory (unqualified, `scope_path` binds to the grant's column and every grant matches every row).
-- WRITE policy: the existing `<table>_isolation` policy stays and now governs writes only; PostgreSQL ORs permissive
-- policies per command, so INSERT/UPDATE/DELETE remain own-user only. Holding a grant is read-only (V2-D53).
--
-- Also: D12 the job idempotency key is unique PER USER (the global key was a cross-tenant existence oracle, T-13);
-- D13 `app_user.id` must be a valid scope segment, because `'/u/' || id || '/'` with a `/` in the id would forge a path
-- inside another tenant's subtree (T-03). If any existing id fails the CHECK this migration fails and changes nothing.
--
-- M9 deviation, recorded: the plan asks for a generated `path_with_prefix` column carrying the `text_pattern_ops`
-- index. It would be an exact copy of `scope_path`, so the index is built on `scope_path` itself; the matching,
-- the index and the planner's use of it are identical.
--
-- Backfill note: the owner is subject to FORCE ROW LEVEL SECURITY, so a role that does not bypass RLS would update zero
-- rows. The backfill therefore lifts FORCE for its own statement and restores it, and disables user triggers so the
-- provenance, clock and lifecycle guards are not re-run over rows whose meaning does not change.

-- 1. D13: refuse ids that could forge a path, now and later.
ALTER TABLE app_user ADD CONSTRAINT app_user_id_is_scope_segment CHECK (
  id !~ '[/[:cntrl:]]' AND id NOT IN ('.', '..') AND id = btrim(id) AND id <> '' AND length(id) <= 256
);

-- 2. One function fills the personal root for writers that name only user_id (every v1 writer).
CREATE FUNCTION fill_scope_path() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.scope_path IS NULL THEN
    NEW.scope_path := '/u/' || NEW.user_id || '/';
  END IF;
  RETURN NEW;
END
$$;

-- 3. Tables that gain both columns, and the two that already have a path and gain only `visibility`.
DO $m2$
DECLARE
  tbl text;
  full_tables text[] := ARRAY[
    'memory_version', 'memory_evidence', 'memory_derivation', 'memory_embedding', 'memory_conflict',
    'turn', 'session', 'job', 'audit_event', 'deletion_request',
    'association', 'abstraction_stats', 'verification'
  ];
  visibility_only text[] := ARRAY['entity', 'negative_constraint'];
BEGIN
  FOREACH tbl IN ARRAY full_tables LOOP
    EXECUTE pg_catalog.format('ALTER TABLE %I ADD COLUMN scope_path text, ADD COLUMN visibility text', tbl);
    -- backfill: every existing row is personal and private
    EXECUTE pg_catalog.format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE pg_catalog.format('ALTER TABLE %I DISABLE TRIGGER USER', tbl);
    EXECUTE pg_catalog.format(
      'UPDATE %I SET scope_path = ''/u/'' || user_id || ''/'', visibility = ''private''', tbl
    );
    EXECUTE pg_catalog.format('ALTER TABLE %I ENABLE TRIGGER USER', tbl);
    EXECUTE pg_catalog.format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE pg_catalog.format(
      'ALTER TABLE %I ALTER COLUMN scope_path SET NOT NULL, ALTER COLUMN visibility SET NOT NULL, '
      'ALTER COLUMN visibility SET DEFAULT ''private'', '
      'ADD CONSTRAINT %I CHECK (scope_path = ''/u/'' || user_id || ''/''), '
      'ADD CONSTRAINT %I CHECK (scope_path ~ ''^/u/[^/[:cntrl:]]+/([a-z0-9][a-z0-9-]*/)*$''), '
      'ADD CONSTRAINT %I CHECK (visibility IN (''private'', ''project'', ''shared''))',
      tbl, tbl || '_scope_matches_user', tbl || '_scope_path_shape', tbl || '_visibility_check'
    );
    EXECUTE pg_catalog.format('CREATE INDEX %I ON %I (scope_path text_pattern_ops)', tbl || '_scope_path_idx', tbl);
    EXECUTE pg_catalog.format(
      'CREATE TRIGGER %I BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION fill_scope_path()',
      'trg_' || tbl || '_scope_default', tbl
    );
  END LOOP;

  FOREACH tbl IN ARRAY visibility_only LOOP
    EXECUTE pg_catalog.format('ALTER TABLE %I ADD COLUMN visibility text NOT NULL DEFAULT ''private''', tbl);
    EXECUTE pg_catalog.format(
      'ALTER TABLE %I ADD CONSTRAINT %I CHECK (visibility IN (''private'', ''project'', ''shared''))',
      tbl, tbl || '_visibility_check'
    );
    EXECUTE pg_catalog.format('CREATE INDEX %I ON %I (scope_path text_pattern_ops)', tbl || '_scope_path_idx', tbl);
  END LOOP;

  -- 4. The read policy on every scoped table (V2-D45).
  FOREACH tbl IN ARRAY full_tables || visibility_only LOOP
    EXECUTE pg_catalog.format(
      'CREATE POLICY %I ON %I FOR SELECT USING ('
      '(pg_catalog.strpos(COALESCE(NULLIF(pg_catalog.current_setting(''app.user_id'', true), ''''), ''/''), ''/'') = 0 AND ('
      'pg_catalog.starts_with(%2$I.scope_path, ''/u/'' || NULLIF(pg_catalog.current_setting(''app.user_id'', true), '''') || ''/'') '
      'OR (%2$I.visibility IN (''project'', ''shared'') AND EXISTS ('
      'SELECT 1 FROM scope_grant AS g WHERE g.principal_id = NULLIF(pg_catalog.current_setting(''app.user_id'', true), '''') '
      'AND g.revoked_at IS NULL AND pg_catalog.starts_with(%2$I.scope_path, g.scope_path))))))',
      tbl || '_scope_read', tbl
    );
  END LOOP;
END
$m2$;

-- 5. D12: the job idempotency key is unique per user, not globally.
ALTER TABLE job DROP CONSTRAINT job_idempotency_key_key;
ALTER TABLE job ADD CONSTRAINT job_user_idempotency_key UNIQUE (user_id, idempotency_key);

-- 6. V2-D54 (S-02): the new function is pinned to the engine schema.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.fill_scope_path() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;
