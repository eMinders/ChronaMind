-- Migration 34: the reconciliation identity sees a restore that keeps the relation (`T-49`, `V2-D128`).
--
-- Migration 33 identified the database the erasure journal was last replayed into by `restore_reconciliation`'s own
-- relation OID. A logical `pg_restore` re-creates the table, so its OID moves -- and a restore that keeps the relation
-- does not move it at all:
--   * `pg_restore --data-only` loads rows back into the existing table, so the identity row it carries, if it lands
--     at all, names the table it is being loaded into (`#262`);
--   * a physical (PITR) restore restores the catalogs and the data together, so the row and the OID both come back;
--   * `CREATE DATABASE ... TEMPLATE` keeps the OID on purpose: a clone of a reconciled database is reconciled.
--
-- The identity becomes the row's own insertion as well as the relation: a new identity row is accepted only when
--   * `relation_oid` is the table's OID now (as before -- it sees a logical restore),
--   * `written_xid` is the transaction the row itself came from (`xmin`): a `COPY` inserts every restored row in the
--     restoring transaction, so a loaded row names a transaction it is not from -- a data-only restore whose identity
--     row lands here is refused even though the OID is unchanged,
--   * `rows_before` is every other row this table holds now: only the last identity row appended to an untouched log
--     can say that. A load that appends its own row next to the one already here is refused, and a later replay
--     appends a row that satisfies the same count -- so a reconciled database keeps working and an older copy of it
--     does not. Appends are serialized with an advisory lock by `deletion/markers.py` so the count is exact,
--   * `timeline` is the cluster timeline the row was written on and the cluster's timeline now. A restore that ends
--     in recovery to a point in time promotes to a new timeline (`pg_control_checkpoint().timeline_id`), so a PITR is
--     refused even where its data is byte-identical to what this database last read.
--
-- All four survive a template clone, which copies the data blocks: the clone's rows keep their `xmin`, the row count
-- and the timeline of the cluster that made it, so the clone of a reconciled database is reconciled -- the property
-- migration 33 was built around, and every test fixture depends on it.
--
-- The primary key goes with it. A replay appends; a restored copy must be able to land its own row beside the one the
-- database already holds, because that appended row is what the next probe rejects (`ON CONFLICT` cannot be used: a
-- dump's `COPY` does not carry one). The table is a short, append-only log, read by a scan of a handful of rows.
--
-- `reconciled_by` and `restored_mode` record *how* a database was reconciled: a fresh install, an operator's assertion
-- that there is no journal to replay, or a replay that was told what kind of restore it is reconciling after
-- (`--restored-mode`). The last one is the acknowledgement a restore the predicate cannot see -- a PITR into a
-- checksum-identical state, a load that omits this table -- needs at the point where the operator acts.

ALTER TABLE restore_reconciliation
  DROP CONSTRAINT restore_reconciliation_pkey,
  ADD COLUMN written_xid     xid8,
  ADD COLUMN rows_before     integer,
  ADD COLUMN timeline        bigint,
  ADD COLUMN reconciled_by   text,
  ADD COLUMN restored_mode   text,
  -- the three witnesses are written together or not at all (a pre-34 row carries none of them, and is refused)
  ADD CONSTRAINT restore_reconciliation_witness_complete
    CHECK ((written_xid IS NULL) = (rows_before IS NULL) AND (written_xid IS NULL) = (timeline IS NULL)),
  ADD CONSTRAINT restore_reconciliation_by_known
    CHECK (reconciled_by IS NULL
           OR reconciled_by IN ('fresh-install', 'operator-assertion', 'replay')),
  ADD CONSTRAINT restore_reconciliation_mode_known
    CHECK (restored_mode IS NULL OR restored_mode IN ('logical', 'data-only', 'physical', 'in-place')),
  -- a fresh install reconciled nothing; every other reconciliation names the restore it was told about
  ADD CONSTRAINT restore_reconciliation_mode_required
    CHECK (restored_mode IS NOT NULL OR reconciled_by IS NULL OR reconciled_by = 'fresh-install');
