-- Migration 12: what the public API needs to authorise and replay a call - E-039 (#97), API_V2 section 1, I9, T-27.
--
-- 1. LINEAGE. `retract_own` and the other `own_lineage_only` operations let a caller touch only what it produced, and the
--    same principal acts as Tier B (a cognition service) and as Tier C (the user): ownership is the pair (principal,
--    tier). `created_by` / `created_tier` record it when an artifact is created through the API. Rows written by v1 code
--    leave both NULL, which the API reads as "the user, as Tier C" (a v1 assertion is the user's own). They are set once
--    and never changed.
-- 2. IDEMPOTENCY. "A write accepts an idempotency_key; a replay returns the original result and never a second effect."
--    `api_idempotency` stores, per (user, operation, key), the id of the result. The identifiers are engine-generated and
--    never chosen by the caller (API_V2 section 1; T-27), so the stored thing is the result id, not a request. The key is
--    scoped to the user, so another tenant's key is never a collision (the oracle D12 closed for jobs). Append-only.
--
-- Additive; the new table is engine infrastructure (it holds ids, never content), so it carries the per-user isolation
-- policy but no scope columns.

ALTER TABLE memory_version
  ADD COLUMN created_by text,
  ADD COLUMN created_tier text,
  ADD CONSTRAINT memory_version_creator_check CHECK (
    (created_by IS NULL AND created_tier IS NULL)
    OR (created_by IS NOT NULL AND btrim(created_by) <> '' AND created_tier IS NOT NULL AND created_tier IN ('B', 'C'))
  );

CREATE FUNCTION guard_creator_fixed() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF (NEW.created_by, NEW.created_tier) IS DISTINCT FROM (OLD.created_by, OLD.created_tier)
     AND (OLD.created_by IS NOT NULL OR OLD.created_tier IS NOT NULL) THEN
    RAISE EXCEPTION 'creator_is_fixed_once_set' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_memory_version_creator_fixed BEFORE UPDATE ON memory_version
  FOR EACH ROW EXECUTE FUNCTION guard_creator_fixed();

CREATE TABLE api_idempotency (
  user_id         text NOT NULL REFERENCES app_user (id),
  operation       text NOT NULL CHECK (btrim(operation) <> ''),
  idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> '' AND length(idempotency_key) <= 256),
  result_id       text NOT NULL CHECK (btrim(result_id) <> ''),
  created_at      timestamptz NOT NULL DEFAULT clock_now(),
  PRIMARY KEY (user_id, operation, idempotency_key)
);

ALTER TABLE api_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_idempotency FORCE ROW LEVEL SECURITY;
CREATE POLICY api_idempotency_isolation ON api_idempotency
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

-- V2-D54 (S-02): the new function is pinned to the engine schema.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_creator_fixed() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;
