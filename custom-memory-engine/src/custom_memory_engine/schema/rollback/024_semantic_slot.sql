-- Rollback of migration 24: semantic slot, cardinality hint, source authority (W3.5).
-- Run in ONE transaction; restores the version-23 schema. Roll back later migrations first.
--
-- DATA LOSS, stated rather than discovered: the slots and cardinality hints recorded since migration 24 have nowhere
-- to live in version 23, and the derived `authority_rank` column is recomputed from `source_type`/`evidence_role`
-- on reapply. Neither is recoverable from another column, which is why this script removes them rather than
-- pretending a downgrade preserved the judgement that produced each `single_valued` decision.
--
-- The exclusion key returns to its version-15 form, and the downgrade PREFLIGHTS the one state version 15 cannot hold.
--
-- Version 24's key is `COALESCE(semantic_slot, predicate_key)`, so two active single-valued beliefs in different slots
-- of the same `(scope_path, subject_key, predicate_key)` range may coexist. Version 15's key is the bare
-- `predicate_key`, and under it those two rows overlap -- which the constraint would refuse. Neither resolution is
-- available inside a downgrade: archiving one needs a covering successor (`enforce_archival_invariant` is deferred and
-- would refuse), and deleting one destroys a memory the caller asserted. So this script *refuses* and says so, counting
-- the collision without naming an id (a rollback is not a place to print memory content), rather than corrupting the
-- ledger or silently destroying a belief. The operator resolves the pair through the engine (supersede or retract)
-- and reruns; that path exists precisely so a human decides which of two contradictory beliefs is the survivor.
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
DROP INDEX IF EXISTS memory_version_semantic_slot_idx;

DO $preflight$
DECLARE
  collisions int;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_attribute
    WHERE attrelid = 'memory_version'::regclass AND attname = 'semantic_slot' AND NOT attisdropped
  ) THEN
    RETURN;  -- already downgraded; nothing to preflight
  END IF;
  -- The exact test, not an approximation: the pair that will collide is any two rows that will both be inside the
  -- version-15 constraint (single-valued, active or archived) and overlap on all three key parts and both ranges.
  EXECUTE $sql$
    SELECT count(*)
    FROM memory_version AS a
    JOIN memory_version AS b
      ON a.id < b.id
     AND a.scope_path = b.scope_path
     AND a.subject_key = b.subject_key
     AND a.predicate_key = b.predicate_key
     AND a.event_range && b.event_range
     AND a.recorded_range && b.recorded_range
    WHERE a.single_valued AND b.single_valued
      AND a.status IN ('active', 'archived') AND b.status IN ('active', 'archived')
  $sql$ INTO collisions;
  IF collisions > 0 THEN
    RAISE EXCEPTION
      'migration_024_downgrade_blocked: % colliding single-valued belief group(s) exist; version 23 cannot hold two '
      'active beliefs under one predicate and slot. Resolve each group through the engine (supersede or retract), then '
      'rerun this rollback.', collisions
      USING ERRCODE = 'P0001';
  END IF;
END
$preflight$;

ALTER TABLE memory_version DROP CONSTRAINT no_bitemporal_overlap;
ALTER TABLE memory_version ADD CONSTRAINT no_bitemporal_overlap EXCLUDE USING gist (
  scope_path WITH =,
  subject_key WITH =,
  predicate_key WITH =,
  event_range WITH &&,
  recorded_range WITH &&
) WHERE (single_valued AND status IN ('active', 'archived'));

ALTER TABLE memory_evidence DROP COLUMN IF EXISTS authority_rank;

ALTER TABLE memory_version
  DROP CONSTRAINT IF EXISTS memory_version_cardinality_hint_check,
  DROP CONSTRAINT IF EXISTS memory_version_semantic_slot_check,
  DROP COLUMN IF EXISTS cardinality_hint,
  DROP COLUMN IF EXISTS semantic_slot;

-- Restore the version-22 `guard_update` (the body migration 22 shipped, without the two migration-24 columns).
CREATE OR REPLACE FUNCTION guard_update() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  is_source_deleted_tombstone boolean;
BEGIN
  -- Belief content is immutable in place, with one narrow V2-D50 exception: retiring a dependent by source deletion
  -- tombstones its content to '' and records retirement_reason = 'source_deleted', and only a deletion may do it.
  is_source_deleted_tombstone := (
    OLD.lifecycle IS DISTINCT FROM 'retired'
    AND NEW.lifecycle = 'retired'
    AND OLD.retirement_reason IS NULL
    AND NEW.retirement_reason = 'source_deleted'
    AND NEW.content = ''
    AND (
      (OLD.recorded_range IS NOT NULL AND NEW.status = 'archived' AND NEW.archival_branch = 'S')
      OR (OLD.recorded_range IS NULL AND NEW.status = 'rejected' AND NEW.archival_branch IS NULL)
    )
    AND deletion_in_this_transaction(OLD.user_id)
  );
  IF is_source_deleted_tombstone THEN
    IF (
         NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.subject_entity_id, NEW.predicate_key,
         NEW.single_valued, NEW.event_range, NEW.layer, NEW.valid_from_basis, NEW.assertion_mode,
         NEW.confidence, NEW.importance, NEW.extractor_version, NEW.kind
       ) IS DISTINCT FROM (
         OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.subject_entity_id, OLD.predicate_key,
         OLD.single_valued, OLD.event_range, OLD.layer, OLD.valid_from_basis, OLD.assertion_mode,
         OLD.confidence, OLD.importance, OLD.extractor_version, OLD.kind
       )
    THEN
      RAISE EXCEPTION 'only status, archival_branch, reinforcement_count and the upper bound of recorded_range may change';
    END IF;
  ELSE
    IF (
         NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.subject_entity_id, NEW.predicate_key,
         NEW.single_valued, NEW.content, NEW.event_range, NEW.layer, NEW.valid_from_basis, NEW.assertion_mode,
         NEW.confidence, NEW.importance, NEW.extractor_version, NEW.kind, NEW.retirement_reason
       ) IS DISTINCT FROM (
         OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.subject_entity_id, OLD.predicate_key,
         OLD.single_valued, OLD.content, OLD.event_range, OLD.layer, OLD.valid_from_basis, OLD.assertion_mode,
         OLD.confidence, OLD.importance, OLD.extractor_version, OLD.kind, OLD.retirement_reason
       )
    THEN
      RAISE EXCEPTION 'only status, archival_branch, reinforcement_count and the upper bound of recorded_range may change';
    END IF;
  END IF;
  IF NOT (NEW.reinforcement_count > OLD.reinforcement_count) THEN
    IF NEW.reinforcement_count IS DISTINCT FROM OLD.reinforcement_count THEN
      RAISE EXCEPTION 'reinforcement_count may only increase';
    END IF;
  END IF;
  IF NEW.archival_branch IS DISTINCT FROM OLD.archival_branch THEN
    IF NOT (OLD.archival_branch IS NULL AND NEW.archival_branch IN ('S', 'C') AND NEW.status = 'archived') THEN
      RAISE EXCEPTION 'archival_branch may only be set by an archive transition (status = archived)';
    END IF;
  END IF;
  IF NEW.recorded_range IS DISTINCT FROM OLD.recorded_range THEN
    IF OLD.recorded_range IS NULL AND NEW.status = 'active' AND upper_inf(NEW.recorded_range)
       AND lower(NEW.recorded_range) = clock_now() THEN
      NULL;
    ELSIF OLD.recorded_range IS NOT NULL AND upper_inf(OLD.recorded_range)
       AND lower(NEW.recorded_range) = lower(OLD.recorded_range)
       AND NOT upper_inf(NEW.recorded_range)
       AND upper(NEW.recorded_range) = clock_now() THEN
      NULL;
    ELSE
      RAISE EXCEPTION 'illegal recorded_range change';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_update() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;

DELETE FROM _cme_schema_migrations WHERE version = 24;
