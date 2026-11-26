-- Migration 33: a deletion's whole live effect survives a restore (`T-46`, `V2-D125`).
--
-- Until now the erasure journal (`V2-D122`) carried one thing per deletion: the HMAC digest of its *target*. A replay
-- into an older backup could only find what the backup already held under that target. Everything else the deletion
-- erased was lost with the database, and two paths re-created it from what the backup still held:
--   * a turn ingested before the backup whose extraction completed (and was deleted) after it: its queued job and the
--     turn itself are restored, and the worker re-extracts the deleted claim;
--   * a write made and deleted after the backup: its `api_idempotency` row is not restored, so the client's
--     resend of the same idempotency key (the app resends the whole history every turn) writes the claim again.
--
-- 1. `erasure_marker`: per deletion request, keyed HMAC digests (never plaintext) of what it erased besides its
--    target -- the idempotency identities of the writes whose results it erased (`write`, with that result's
--    engine-generated id) and the evidence turns of the versions it physically deleted (`turn`; for `all`, every turn
--    of the user). Written in the deletion's own transaction, exported with the journal, and inserted again by the
--    replay, which first uses the `turn` digests to find what the restored database still holds. `api.runtime.replayed`
--    reads `write` markers, so a resend after the replay answers with the erased result instead of writing it again.
--    Append-only for the runtime role, like `deletion_request`, whose `(id, user_id)` it references.
-- 2. `erased_turn`: a turn a deletion marked that is still stored (another surviving version cites it, or a running
--    job held it). The worker refuses to extract such a turn, so a re-extraction (a new extractor version, a restored
--    job) cannot re-derive what was deleted. It holds the turn's own id and goes with the turn (`ON DELETE CASCADE`),
--    so it names nothing the `turn` table does not already hold, and the worker needs no deletion key to read it.
-- 3. `restore_reconciliation`: the identity of the database the erasure journal was last reconciled with. `pg_restore`
--    re-creates this table, so it has a new relation OID and the restored copy no longer matches the row it carries:
--    the worker claims nothing and the host does not start until the replay records the new identity. That turns
--    "replay before ANY worker runs" from a step of the procedure into a check.
--
--    The identity is this table's own OID, not the database's: `CREATE DATABASE ... TEMPLATE` (a clone, which every
--    test fixture makes, and an operator may) keeps every relation OID and changes only the database's, and a clone of
--    a reconciled database is a reconciled database. The reverse -- a distinct database whose relation happens to take
--    a recorded OID -- needs the OID counter to wrap. Not detected: a data-only restore into the same relation, and a
--    physical (PITR) restore, which keeps every OID; the procedure replays after any restore regardless. The cluster's
--    system identifier is not used either, because `pg_control_system()` is not executable by the runtime role.
-- 4. `api_idempotency (user_id, result_id)`: deletion reads the identities of the results it deletes.

ALTER TABLE deletion_request ADD CONSTRAINT deletion_request_id_user_id_key UNIQUE (id, user_id);

CREATE TABLE erasure_marker (
  request_id text NOT NULL,
  user_id    text NOT NULL REFERENCES app_user (id),
  kind       text NOT NULL CHECK (kind IN ('write', 'turn')),
  digest     text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
  -- the engine-generated id a `write` answered with (random, never content: T-27); empty for a turn
  result_id  text NOT NULL DEFAULT '' CHECK (length(result_id) <= 256),
  PRIMARY KEY (request_id, kind, digest),
  CONSTRAINT erasure_marker_request_same_tenant
    FOREIGN KEY (request_id, user_id) REFERENCES deletion_request (id, user_id),
  CONSTRAINT erasure_marker_result_only_for_writes CHECK ((kind = 'write') = (result_id <> ''))
);
CREATE INDEX erasure_marker_lookup ON erasure_marker (user_id, kind, digest);

ALTER TABLE erasure_marker ENABLE ROW LEVEL SECURITY;
ALTER TABLE erasure_marker FORCE ROW LEVEL SECURITY;
CREATE POLICY erasure_marker_isolation ON erasure_marker
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));

CREATE TABLE erased_turn (
  turn_id text NOT NULL,
  user_id text NOT NULL,
  PRIMARY KEY (turn_id, user_id),
  CONSTRAINT erased_turn_same_tenant FOREIGN KEY (turn_id, user_id) REFERENCES turn (id, user_id) ON DELETE CASCADE
);

ALTER TABLE erased_turn ENABLE ROW LEVEL SECURITY;
ALTER TABLE erased_turn FORCE ROW LEVEL SECURITY;
CREATE POLICY erased_turn_isolation ON erased_turn
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));

CREATE INDEX api_idempotency_result ON api_idempotency (user_id, result_id);

CREATE TABLE restore_reconciliation (
  relation_oid  oid PRIMARY KEY,
  reconciled_at timestamptz NOT NULL DEFAULT clock_now()
);

-- deployment state, not tenant data: every tenant reads it, the replay appends to it, nothing rewrites it
ALTER TABLE restore_reconciliation ENABLE ROW LEVEL SECURITY;
ALTER TABLE restore_reconciliation FORCE ROW LEVEL SECURITY;
CREATE POLICY restore_reconciliation_read ON restore_reconciliation FOR SELECT USING (true);
CREATE POLICY restore_reconciliation_append ON restore_reconciliation FOR INSERT WITH CHECK (true);

-- This migration deliberately does NOT record an identity for itself. A dump taken before 33 and restored is
-- *migrated* too (deploy/README.md restores it at its own version, then migrates), so a stamp written here would mark a
-- restored copy as reconciled with no replay -- exactly the state the check exists to catch. A fresh install is stamped
-- by `MigrationRunner.apply` after the schema is created from nothing (`schema/migrate.py`), and an upgraded or
-- restored database is stamped by the journal replay, or by `scripts/erasure_journal.py replay --mark-reconciled` when
-- an operator asserts there is no journal to replay.
