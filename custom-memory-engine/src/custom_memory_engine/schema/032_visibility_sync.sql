-- Migration 32: owner-controlled visibility sync across child artifact tables (`I7`, `V2-D113`).
--
-- Scope read policies (`011_scope.sql`) check `scope_path` and `visibility` independently on `memory_version`,
-- `memory_evidence`, `memory_derivation`, `memory_embedding`, `abstraction_stats`, `verification`, `entity`, and
-- `association`. Updating `memory_version.visibility` alone would leave child provenance/embedding/ledger/verification
-- rows `private`, making a shared artifact unrenderable (`missing_provenance`) to a grantee.
--
-- Adds:
--   1. Updated `guard_verification_append_only()` permitting ONLY a visibility sync that matches the parent
--      `memory_version.visibility` (all verification fields stay strictly append-only; `TRUNCATE` still raises).
--   2. `set_artifact_visibility(p_user_id, p_artifact_id, p_visibility)` (`SECURITY DEFINER`, pinned `search_path`),
--      callable only for the session's active `app.user_id` inside `/u/<user>/`, updating `memory_version` + child rows
--      (or `entity` / `association`) atomically.

CREATE OR REPLACE FUNCTION guard_verification_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF (
         NEW.id, NEW.version_id, NEW.user_id, NEW.held_out_split_id, NEW.verified_on,
         NEW.negative_control_slice_id, NEW.support_window, NEW.held_out_window,
         NEW.support_context_key, NEW.held_out_context_key, NEW.outcome,
         NEW.scope_path, NEW.artifact_pinning_status
       ) IS NOT DISTINCT FROM (
         OLD.id, OLD.version_id, OLD.user_id, OLD.held_out_split_id, OLD.verified_on,
         OLD.negative_control_slice_id, OLD.support_window, OLD.held_out_window,
         OLD.support_context_key, OLD.held_out_context_key, OLD.outcome,
         OLD.scope_path, OLD.artifact_pinning_status
       )
       AND NEW.visibility IN ('private', 'project', 'shared')
       AND NEW.visibility = (
         SELECT mv.visibility FROM memory_version AS mv WHERE mv.id = NEW.version_id AND mv.user_id = NEW.user_id
       ) THEN
      RETURN NEW;
    END IF;
  END IF;
  RAISE EXCEPTION 'verification_is_append_only' USING ERRCODE = 'P0001';
END
$$;

CREATE FUNCTION set_artifact_visibility(p_user_id text, p_artifact_id text, p_visibility text) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_scope text;
BEGIN
  IF p_visibility NOT IN ('private', 'project', 'shared') THEN
    RAISE EXCEPTION 'visibility_invalid' USING ERRCODE = '22023';
  END IF;
  IF NULLIF(pg_catalog.current_setting('app.user_id', true), '') IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'visibility_change_forbidden' USING ERRCODE = '42501';
  END IF;

  UPDATE memory_version AS mv
     SET visibility = p_visibility
   WHERE mv.id = p_artifact_id
     AND mv.user_id = p_user_id
     AND pg_catalog.starts_with(mv.scope_path, '/u/' || p_user_id || '/')
  RETURNING mv.scope_path INTO v_scope;

  IF FOUND THEN
    UPDATE memory_evidence
       SET scope_path = v_scope, visibility = p_visibility
     WHERE version_id = p_artifact_id AND user_id = p_user_id;
    UPDATE memory_derivation
       SET scope_path = v_scope, visibility = p_visibility
     WHERE derived_version_id = p_artifact_id AND user_id = p_user_id;
    UPDATE memory_embedding
       SET scope_path = v_scope, visibility = p_visibility
     WHERE version_id = p_artifact_id AND user_id = p_user_id;
    UPDATE abstraction_stats
       SET scope_path = v_scope, visibility = p_visibility
     WHERE version_id = p_artifact_id AND user_id = p_user_id;
    UPDATE verification
       SET visibility = p_visibility
     WHERE version_id = p_artifact_id AND user_id = p_user_id;
    RETURN 'memory_version';
  END IF;

  UPDATE entity AS e
     SET visibility = p_visibility
   WHERE e.id = p_artifact_id
     AND e.user_id = p_user_id
     AND pg_catalog.starts_with(e.scope_path, '/u/' || p_user_id || '/');
  IF FOUND THEN
    RETURN 'entity';
  END IF;

  UPDATE association AS a
     SET visibility = p_visibility
   WHERE a.id = p_artifact_id
     AND a.user_id = p_user_id
     AND pg_catalog.starts_with(a.scope_path, '/u/' || p_user_id || '/');
  IF FOUND THEN
    RETURN 'association';
  END IF;

  RETURN NULL;
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'guard_verification_append_only()',
    'set_artifact_visibility(text, text, text)'
  ] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
