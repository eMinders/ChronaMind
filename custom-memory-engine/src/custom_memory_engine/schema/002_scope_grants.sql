-- Migration 2: scope membership (ACL) storage — V2-D36 (resolves B-02).
--
-- ARCHITECTURE_V2.md §5 rules 2–3 require that a `project`/`shared` row be readable
-- only by a caller "whose ACL grants the caller membership". No storage existed.
-- This is the minimum model that makes those rules implementable and fail-closed:
--
--   * a grant gives ONE principal read membership of ONE scope subtree;
--   * only the owner of a user namespace (/u/<owner>/...) may grant within it, so a
--     tenant can never grant access to another tenant's scope (no implicit sharing);
--   * `private` rows are never readable through a grant (rule 3);
--   * revocation is append-only: `revoked_at` may be set once and never changed,
--     so the history of who could read what, and when, is auditable.
--
-- Additive only: no existing table, column, policy or function is altered.

CREATE TABLE scope_grant (
  id              text PRIMARY KEY,
  owner_user_id   text NOT NULL REFERENCES app_user (id),
  principal_id    text NOT NULL REFERENCES app_user (id),
  scope_path      text NOT NULL,
  role            text NOT NULL DEFAULT 'member' CHECK (role IN ('member')),
  granted_at      timestamptz NOT NULL,
  revoked_at      timestamptz,
  CONSTRAINT scope_grant_path_canonical CHECK (
    scope_path ~ '^/u/[^/[:cntrl:]]+/([a-z0-9][a-z0-9-]*/)*$'
  ),
  -- Exact literal prefix (never LIKE: B-01). The owner may grant only inside /u/<owner>/.
  CONSTRAINT scope_grant_within_owner_namespace CHECK (
    pg_catalog.starts_with(scope_path, '/u/' || owner_user_id || '/')
  ),
  CONSTRAINT scope_grant_not_to_self CHECK (principal_id <> owner_user_id),
  CONSTRAINT scope_grant_revocation_ordered CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);

CREATE UNIQUE INDEX scope_grant_active_unique
  ON scope_grant (owner_user_id, principal_id, scope_path) WHERE revoked_at IS NULL;
CREATE INDEX scope_grant_principal_path_idx
  ON scope_grant (principal_id, scope_path text_pattern_ops) WHERE revoked_at IS NULL;

-- Only revocation is a legal update; identity, path and grant time are immutable.
CREATE FUNCTION guard_scope_grant_update() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.revoked_at IS NOT NULL THEN
    RAISE EXCEPTION 'scope_grant_already_revoked' USING ERRCODE = 'P0001';
  END IF;
  IF (NEW.id, NEW.owner_user_id, NEW.principal_id, NEW.scope_path, NEW.role, NEW.granted_at)
     IS DISTINCT FROM
     (OLD.id, OLD.owner_user_id, OLD.principal_id, OLD.scope_path, OLD.role, OLD.granted_at) THEN
    RAISE EXCEPTION 'scope_grant_only_revocation_may_change' USING ERRCODE = 'P0001';
  END IF;
  IF NEW.revoked_at IS NULL THEN
    RAISE EXCEPTION 'scope_grant_update_must_revoke' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_scope_grant_update
  BEFORE UPDATE ON scope_grant
  FOR EACH ROW
  EXECUTE FUNCTION guard_scope_grant_update();

ALTER TABLE scope_grant ENABLE ROW LEVEL SECURITY;
ALTER TABLE scope_grant FORCE ROW LEVEL SECURITY;
-- Owner and grantee may see a grant; nobody else learns that it exists.
CREATE POLICY scope_grant_read ON scope_grant FOR SELECT
  USING (NULLIF(current_setting('app.user_id', true), '') IN (owner_user_id, principal_id));
CREATE POLICY scope_grant_insert ON scope_grant FOR INSERT
  WITH CHECK (owner_user_id = NULLIF(current_setting('app.user_id', true), ''));
CREATE POLICY scope_grant_update ON scope_grant FOR UPDATE
  USING (owner_user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (owner_user_id = NULLIF(current_setting('app.user_id', true), ''));
CREATE POLICY scope_grant_delete ON scope_grant FOR DELETE
  USING (owner_user_id = NULLIF(current_setting('app.user_id', true), ''));
