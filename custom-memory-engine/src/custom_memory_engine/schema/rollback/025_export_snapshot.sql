-- Rollback of migration 25: the durable export manifest (E-038 / F11).
-- Run in ONE transaction; restores the version-24 schema. Roll back later migrations first.
--
-- It REFUSES while any snapshot exists (`m25_rollback_refused_export_snapshots_exist`), like rollbacks 8 and 11: every
-- manifest captured since migration 25 exists only in this table -- what was exported under which deletion epoch -- and
-- it is not derivable from the corpus, which has moved since. An operator who has kept the manifests elsewhere and
-- accepts losing them here says so in the same transaction, naming exactly that loss:
--
--   SET LOCAL cme.rollback_discard = 'export_snapshot';
--
-- A setting rather than a flag on the script, because it is transaction-local (it cannot leak into a later session),
-- it names the one thing being discarded (acknowledging one table does not acknowledge another), and an empty value
-- -- the default -- refuses. The runtime role's grant on it is
-- withdrawn by the provisioning pass (`security/provisioning.py`) once the table is gone; no grant is rewritten here,
-- because a downgrade must not silently widen or narrow the role.
-- This rollback's data guard must see every tenant's rows. Under FORCE ROW LEVEL SECURITY a role that cannot bypass it (an
-- ordinary owner included) sees none, so the guard would pass on an empty view and the rollback would drop data it
-- was written to protect. Such a role is refused first (`V2-D123`).
DO $bypass$
BEGIN
  IF NOT (SELECT rolsuper OR rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
    RAISE EXCEPTION 'rollback_requires_bypassrls' USING ERRCODE = '42501';
  END IF;
END
$bypass$;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM export_snapshot)
     AND COALESCE(pg_catalog.current_setting('cme.rollback_discard', true), '') <> 'export_snapshot' THEN
    RAISE EXCEPTION 'm25_rollback_refused_export_snapshots_exist' USING ERRCODE = 'P0001';
  END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_export_snapshot_no_truncate ON export_snapshot;
DROP TRIGGER IF EXISTS trg_export_snapshot_append_only ON export_snapshot;
DROP TABLE IF EXISTS export_snapshot;
DROP FUNCTION IF EXISTS guard_export_snapshot_append_only();

-- The history row goes last, and only after the catalog is back at version 24: `migrate.status()` validates that the
-- recorded history is a gap-free prefix of the declared migrations, so a row left behind by a rollback would report the
-- schema as drifted rather than as version 24.
DELETE FROM _cme_schema_migrations WHERE version = 25;
