-- Rollback of migration 13 (E-039). Restores the version-12 schema exactly. It REFUSES while any artifact is written
-- under one of the two predicates, because removing the catalog row would orphan it (the foreign key would refuse anyway).
-- Roll back later migrations first.

DO $guard$
BEGIN
  IF EXISTS (SELECT 1 FROM memory_version WHERE predicate_key IN ('episode', 'statement')) THEN
    RAISE EXCEPTION 'm13_rollback_refused_generic_predicates_in_use' USING ERRCODE = 'P0001';
  END IF;
END
$guard$;

DELETE FROM predicate_schema WHERE predicate_key IN ('episode', 'statement');
DELETE FROM _cme_schema_migrations WHERE version = 13;
