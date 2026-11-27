-- Rollback of migration 34: the restore identity's own witnesses (`T-49`, `V2-D128`).
-- Run in ONE transaction; restores the version-33 schema.
--
-- The identity rows go, not the columns alone. Version-33 code accepts a database whose table carries *any* row for
-- this relation, and a row this version appended is only meaningful together with `written_xid`: keeping it would
-- hand version 33 a reconciled database where this version deliberately says the identity cannot be trusted (an
-- upgrade in place, which has been replayed into neither version). So the table is emptied here, and the version-33
-- deployment replays -- or asserts `--mark-reconciled` -- exactly as it does after upgrading to version 33.
--
-- That DELETE must see every row, and `restore_reconciliation` is `FORCE ROW LEVEL SECURITY` with read and append
-- policies only: an ordinary owner (this rollback runs as the migration owner) deletes nothing and the rollback would
-- leave rows it was written to remove. Such a role is refused first (`V2-D123`), as rollbacks 25 and 33's are.
DO $bypass$
BEGIN
  IF NOT (SELECT rolsuper OR rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
    RAISE EXCEPTION 'rollback_requires_bypassrls' USING ERRCODE = '42501';
  END IF;
END
$bypass$;

DELETE FROM restore_reconciliation;

ALTER TABLE restore_reconciliation
  DROP CONSTRAINT restore_reconciliation_mode_required,
  DROP CONSTRAINT restore_reconciliation_mode_known,
  DROP CONSTRAINT restore_reconciliation_by_known,
  DROP CONSTRAINT restore_reconciliation_witness_complete,
  DROP COLUMN restored_mode,
  DROP COLUMN reconciled_by,
  DROP COLUMN timeline,
  DROP COLUMN rows_before,
  DROP COLUMN written_xid,
  ADD CONSTRAINT restore_reconciliation_pkey PRIMARY KEY (relation_oid);

DELETE FROM _cme_schema_migrations WHERE version = 34;
