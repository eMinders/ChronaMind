-- Rollback of migration 13 (E-039). Restores the version-12 schema exactly. It REFUSES while any artifact is written
-- under one of the two predicates, because removing the catalog row would orphan it (the foreign key would refuse anyway).
-- Roll back later migrations first.
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

DO $guard$
BEGIN
  IF EXISTS (SELECT 1 FROM memory_version WHERE predicate_key IN ('episode', 'statement')) THEN
    RAISE EXCEPTION 'm13_rollback_refused_generic_predicates_in_use' USING ERRCODE = 'P0001';
  END IF;
END
$guard$;

DELETE FROM predicate_schema WHERE predicate_key IN ('episode', 'statement');
DELETE FROM _cme_schema_migrations WHERE version = 13;
