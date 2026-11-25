-- Migration 31: verification artifact pinning (`I11`, `V2-D112`).
--
-- Previously `verification` recorded split windows and context keys with no artifact IDs at all, permitting a
-- `verified` status with an empty held-out set.
--
-- Adds:
--   1. `verification.artifact_pinning_status IN ('pinned', 'unpinned_pre_migration')` — pre-existing rows carry
--      `'unpinned_pre_migration'` honestly; new rows default to `'pinned'`.
--   2. `verification_artifact(verification_id, user_id, role, artifact_id, created_at)` with
--      `PRIMARY KEY (verification_id, artifact_id)` so SQL itself guarantees the same artifact cannot appear in both
--      `'support'` and `'held_out'` for one verification. Intentionally carries NO foreign key on `artifact_id` because
--      append-only verification records must outlive deleted memories (the IDs are opaque and carry no content).
--   3. Deferred constraint trigger `enforce_verification_artifacts()` requiring at least one `'support'` and one
--      `'held_out'` artifact row for any `'pinned'` verification at commit.

ALTER TABLE verification
  ADD CONSTRAINT verification_id_user_unique UNIQUE (id, user_id),
  ADD COLUMN artifact_pinning_status text NOT NULL DEFAULT 'unpinned_pre_migration'
  CONSTRAINT verification_pinning_status_check CHECK (
    artifact_pinning_status IN ('pinned', 'unpinned_pre_migration')
  );

ALTER TABLE verification
  ALTER COLUMN artifact_pinning_status SET DEFAULT 'pinned';

CREATE TABLE verification_artifact (
  verification_id text NOT NULL,
  user_id         text NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('support', 'held_out')),
  artifact_id     text NOT NULL CHECK (btrim(artifact_id) <> ''),
  created_at      timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
  PRIMARY KEY (verification_id, artifact_id),
  CONSTRAINT verification_artifact_parent_same_tenant
    FOREIGN KEY (verification_id, user_id) REFERENCES verification (id, user_id) ON DELETE CASCADE
);

CREATE INDEX verification_artifact_lookup_idx
  ON verification_artifact (user_id, verification_id, role);

ALTER TABLE verification_artifact ENABLE ROW LEVEL SECURITY;
ALTER TABLE verification_artifact FORCE ROW LEVEL SECURITY;
CREATE POLICY verification_artifact_isolation ON verification_artifact
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));
CREATE POLICY verification_artifact_scope_read ON verification_artifact FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM verification AS ver
       WHERE ver.id = verification_artifact.verification_id
         AND ver.user_id = verification_artifact.user_id
    )
  );

CREATE FUNCTION guard_verification_artifact_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF NOT EXISTS (SELECT 1 FROM verification WHERE id = OLD.verification_id) THEN
      RETURN OLD;
    END IF;
  END IF;
  RAISE EXCEPTION 'verification_is_append_only' USING ERRCODE = 'P0001';
END
$$;

CREATE TRIGGER trg_verification_artifact_append_only
  BEFORE UPDATE OR DELETE ON verification_artifact
  FOR EACH ROW
  EXECUTE FUNCTION guard_verification_artifact_append_only();

CREATE TRIGGER trg_verification_artifact_no_truncate
  BEFORE TRUNCATE ON verification_artifact
  FOR EACH STATEMENT
  EXECUTE FUNCTION guard_verification_artifact_append_only();

CREATE FUNCTION enforce_verification_artifacts() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  pinning text;
BEGIN
  SELECT artifact_pinning_status INTO pinning
    FROM verification WHERE id = NEW.id AND user_id = NEW.user_id;
  IF NOT FOUND OR pinning = 'unpinned_pre_migration' THEN
    RETURN NULL;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM verification_artifact
     WHERE verification_id = NEW.id AND user_id = NEW.user_id AND role = 'support'
  ) THEN
    RAISE EXCEPTION 'verification_support_artifacts_required' USING ERRCODE = 'P0001';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM verification_artifact
     WHERE verification_id = NEW.id AND user_id = NEW.user_id AND role = 'held_out'
  ) THEN
    RAISE EXCEPTION 'verification_held_out_artifacts_required' USING ERRCODE = 'P0001';
  END IF;
  RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER trg_verification_requires_artifacts
  AFTER INSERT ON verification
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION enforce_verification_artifacts();

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'guard_verification_artifact_append_only()',
    'enforce_verification_artifacts()'
  ] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
