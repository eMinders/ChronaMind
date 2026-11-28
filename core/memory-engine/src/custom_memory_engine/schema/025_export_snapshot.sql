-- Migration 25: `export_snapshot` -- a durable, epoch-bound export manifest (E-038 / F11, channel 11, V2-D99).
--
-- `export` was in the contract with no handler, and what existed was `deletion.closure.ExportSnapshotStore`: a Python
-- dict in one process. That was enough to prove F11 inside one test, and useless as an implementation -- the host builds
-- its wiring per call, so an export captured by one request would never be served by the next, and a second worker
-- would not know it existed at all. Channel 11 ("a snapshot made before a deletion must not be served after it") needs a
-- record that outlives the process that made it, so this migration adds the table the handler writes.
--
-- What is stored is a **manifest**, not the memories: the sorted ids the caller could read, a digest over them, how many
-- there were, and the deletion epoch they were captured under. Content stays where it already lives, behind the read
-- path's own predicates, so an export cannot become a second copy of the corpus that a deletion does not reach -- which
-- is exactly how an export turns into a leak (channel 11). Serving a snapshot therefore re-reads through `query`; the
-- row is the *receipt* of what the export contained and the proof of which epoch it is valid for.
--
-- The epoch is what makes F11 enforceable rather than aspirational: `delete_memory` appends a `deletion_request` whose
-- `deletion_epoch` is unique per user, and `current_deletion_epoch` is the MAX of those. A snapshot captured at epoch E
-- is servable only while the caller's current epoch is still E. No trigger is needed -- the comparison is a read of an
-- append-only table -- and the guard below refuses the one edit that would fake currency: changing `captured_epoch`.
--
-- Append-only, and for the same reason `audit_event` and `deletion_request` are: a snapshot that could be rewritten
-- after the fact is not evidence of what was exported. `UPDATE` is refused outright and `DELETE` is refused while the
-- parent user exists; a row goes only with its user, by cascade, which is the one legal removal.

CREATE TABLE export_snapshot (
  id               text PRIMARY KEY,
  user_id          text NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
  captured_epoch   integer NOT NULL CHECK (captured_epoch >= 0),
  artifact_count   integer NOT NULL CHECK (artifact_count >= 0),
  -- SHA-256 over the sorted ids: the same set exported twice produces the same digest, and a manifest that no longer
  -- matches its own digest cannot be served.
  manifest_digest  text NOT NULL CHECK (manifest_digest ~ '^[a-f0-9]{64}$'),
  -- The ids themselves, sorted. A JSON array of strings, so PostgreSQL validates the type rather than trusting a blob.
  artifact_ids     jsonb NOT NULL CHECK (pg_catalog.jsonb_typeof(artifact_ids) = 'array'),
  -- The read set the export covered, as the caller named it. Paths, not content, and bounded by the selector cap.
  scope_paths      text[] NOT NULL CHECK (pg_catalog.array_length(scope_paths, 1) BETWEEN 1 AND 8),
  captured_at      timestamptz NOT NULL DEFAULT clock_now()
);
CREATE INDEX export_snapshot_user_idx ON export_snapshot (user_id, captured_at DESC);

-- The manifest must describe itself: the count is what the caller was told, so it must equal the array's length.
ALTER TABLE export_snapshot ADD CONSTRAINT export_snapshot_manifest_matches_count
  CHECK (pg_catalog.jsonb_array_length(artifact_ids) = artifact_count);

CREATE FUNCTION guard_export_snapshot_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'export_snapshot_is_append_only' USING ERRCODE = 'P0001';
END
$$;

CREATE TRIGGER trg_export_snapshot_append_only BEFORE UPDATE OR DELETE ON export_snapshot
  FOR EACH ROW EXECUTE FUNCTION guard_export_snapshot_append_only();
CREATE TRIGGER trg_export_snapshot_no_truncate BEFORE TRUNCATE ON export_snapshot
  FOR EACH STATEMENT EXECUTE FUNCTION guard_export_snapshot_append_only();

ALTER TABLE export_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE export_snapshot FORCE ROW LEVEL SECURITY;
-- Per-user isolation and nothing else: a manifest holds ids and paths, never content, so it needs no scope columns and
-- no grant-reading policy. `user_id = app.user_id` is the whole rule, exactly as `api_idempotency` has it.
CREATE POLICY export_snapshot_isolation ON export_snapshot
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

-- V2-D54 (S-02): the new function is pinned to the engine schema.
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
