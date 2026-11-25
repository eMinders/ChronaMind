-- Rollback of migration 25: the durable export manifest (E-038 / F11).
-- Run in ONE transaction; restores the version-24 schema. Roll back later migrations first.
--
-- DATA LOSS, stated rather than discovered: every snapshot captured since migration 25 exists only in this table. A
-- downgrade discards the record of what was exported under which deletion epoch, and nothing else in the schema holds
-- it -- the manifests are not derivable from the corpus, because the corpus has moved since. An operator who needs the
-- manifest should export it before rolling back; this script does not pretend a downgrade preserved it.
--
-- Nothing to preflight: the table is additive and no other relation references it. The runtime role's grant on it is
-- withdrawn by the provisioning pass (`security/provisioning.py`) once the table is gone; no grant is rewritten here,
-- because a downgrade must not silently widen or narrow the role.
DROP TRIGGER IF EXISTS trg_export_snapshot_no_truncate ON export_snapshot;
DROP TRIGGER IF EXISTS trg_export_snapshot_append_only ON export_snapshot;
DROP TABLE IF EXISTS export_snapshot;
DROP FUNCTION IF EXISTS guard_export_snapshot_append_only();

-- The history row goes last, and only after the catalog is back at version 24: `migrate.status()` validates that the
-- recorded history is a gap-free prefix of the declared migrations, so a row left behind by a rollback would report the
-- schema as drifted rather than as version 24.
DELETE FROM _cme_schema_migrations WHERE version = 25;
