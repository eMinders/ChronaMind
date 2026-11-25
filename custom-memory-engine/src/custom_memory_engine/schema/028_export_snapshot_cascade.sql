-- Migration 28: a manifest goes with its user by cascade, and only that way (PR #241 review).
--
-- Migration 25's header promised that `DELETE` on `export_snapshot` is refused while the parent user exists and that a
-- row goes only with its user, by cascade. The guard raised unconditionally, so the cascade from `app_user` raised too
-- and an exporting user could never be removed -- which would block any account-erasure path.
--
-- The guard now distinguishes the two by the one fact that separates them: whether the parent row still exists. A
-- cascade from `DELETE FROM app_user` fires the row trigger after the parent is gone in the same statement, so the
-- parent is not visible; a direct `DELETE FROM export_snapshot` finds its parent and is refused. `UPDATE` and
-- `TRUNCATE` are refused exactly as before. The lookup runs as the function owner under the pinned search_path, and
-- RLS on `app_user` binds the owner too, so it reads with the row's own user as tenant.
--
-- Migration 25's text is not edited: migrations are checksum-bound. This replaces the function body only.

CREATE OR REPLACE FUNCTION guard_export_snapshot_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  previous_tenant text := pg_catalog.current_setting('app.user_id', true);
BEGIN
  IF TG_OP = 'DELETE' THEN
    -- Reading `app_user` under FORCE RLS needs the row's own tenant in `app.user_id`; the setting is transaction-local
    -- and restored below, so a cascading delete does not leave another tenant bound for the rest of the transaction.
    PERFORM pg_catalog.set_config('app.user_id', OLD.user_id, true);
    IF NOT EXISTS (SELECT 1 FROM app_user WHERE id = OLD.user_id) THEN
      PERFORM pg_catalog.set_config('app.user_id', COALESCE(previous_tenant, ''), true);
      RETURN OLD;  -- the parent is gone in this statement: the cascade the header promises
    END IF;
    PERFORM pg_catalog.set_config('app.user_id', COALESCE(previous_tenant, ''), true);
  END IF;
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
