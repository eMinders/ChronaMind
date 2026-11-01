-- Migration 5: independent axes on memory_version (M1 + M7) - E-010, V2-D01, V2-D38, V2-D43, V2-D48, V2-D49.
--
-- v1 classified a row with one enum (`layer`) plus `status`. V2 has three independent axes:
--   kind      in {episode, claim, procedure, abstraction}   fixed at creation, immutable
--   lifecycle in {observed, asserted, inferred, contested, retired}
--   retention in {working, session, durable}
--
-- Decisions followed (not re-opened):
--   V2-D38  the v1 -> V2 mapping is `custom_memory_engine.model.axes.map_v1_version` / `V1_BACKFILL_SQL`;
--           `archived` is a *lifecycle* (`retired`) with `retention = durable` (M7), never a retention value;
--           staging rows (`candidate`, `rejected`) are `retired`; `conflict_pending` is `contested`.
--   V2-D49 D6  `layer` stays STORED and is dual-written (semantic and opinion are both `claim`, so it is the
--           only lossless discriminator); it is not dropped here.
--   V2-D49 D7  `persona` -> `abstraction` / `inferred` (the zero ledger belongs to E-014's tables).
--   V2-D48 C-P1  kind-conditioned legality of a lifecycle is enforced by E-016, not here; every value this
--           migration writes is legal for its kind.
--
-- Dual-write window. ONE trigger function, `sync_memory_version_axes`, holds the v1 -> V2 mapping and is the only
-- place it is written in SQL. It is used by this migration's backfill (an UPDATE that fires it) and by every later
-- INSERT/UPDATE, so no v1 write path (`engine/transactions.py`, `engine/ingest.py`,
-- `consolidation/consolidator.py`, `deletion/rederive.py`) can write an inconsistent `(layer, status)` /
-- `(kind, lifecycle, retention)` pair, and none had to change. Code that writes the axes itself is validated
-- against `layer`/`status` instead of overwritten. The window ends when the last v1 reader of `layer`/`status` is
-- removed (an owner decision, not made here); removing it is dropping the two triggers and `layer`.
--
-- Additive: 001-003 are not edited. New columns are appended (positions 21-23), so positional INSERTs of columns
-- 1-10 in the bitemporal scripts are unaffected. `guard_update` is replaced (CREATE OR REPLACE) with `kind` added
-- to its immutable tuple.

ALTER TABLE memory_version
  ADD COLUMN kind text,
  ADD COLUMN lifecycle text,
  ADD COLUMN retention text;

CREATE FUNCTION sync_memory_version_axes() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  derived_kind text;
  derived_lifecycle text;
  recompute boolean;
  has_derivation boolean;
BEGIN
  derived_kind := CASE NEW.layer
    WHEN 'episodic' THEN 'episode' WHEN 'semantic' THEN 'claim'
    WHEN 'opinion' THEN 'claim' WHEN 'persona' THEN 'abstraction' END;

  -- kind: derived when absent (v1 writers, backfill); validated against `layer` when a caller supplies it. v1 has
  -- no procedure layer, so `procedure` is the one kind a caller may supply that `layer` cannot imply (how its
  -- `layer` column is filled is owned by E-030; v1 code never writes it).
  IF NEW.kind IS NULL THEN
    NEW.kind := derived_kind;
  ELSIF NEW.kind <> derived_kind AND NEW.kind <> 'procedure' THEN
    RAISE EXCEPTION 'axes_kind_inconsistent_with_layer' USING ERRCODE = 'P0001';
  END IF;

  IF NEW.retention IS NULL THEN
    NEW.retention := 'durable';
  END IF;

  -- lifecycle: derived on insert when absent, and re-derived when a v1 writer changes `status` without
  -- touching the lifecycle (the v1 status transitions). A caller that sets the lifecycle itself is validated.
  recompute := NEW.lifecycle IS NULL
    OR (TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status AND NEW.lifecycle IS NOT DISTINCT FROM OLD.lifecycle);
  IF recompute THEN
    has_derivation := false;
    IF NEW.status = 'active' AND NEW.layer IN ('semantic', 'opinion') THEN
      has_derivation := EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id);
    END IF;
    derived_lifecycle := CASE NEW.status
      WHEN 'active' THEN CASE
        WHEN NEW.layer = 'episodic' THEN 'observed'
        WHEN NEW.layer = 'persona' THEN 'inferred'
        WHEN has_derivation THEN 'inferred'
        ELSE 'asserted' END
      WHEN 'archived' THEN 'retired'
      WHEN 'conflict_pending' THEN 'contested'
      WHEN 'rejected' THEN 'retired'
      WHEN 'candidate' THEN 'retired' END;
    NEW.lifecycle := derived_lifecycle;
  ELSIF NOT (
    (NEW.status = 'active' AND NEW.lifecycle IN ('observed', 'asserted', 'inferred'))
    OR (NEW.status = 'conflict_pending' AND NEW.lifecycle = 'contested')
    OR (NEW.status IN ('archived', 'rejected', 'candidate') AND NEW.lifecycle = 'retired')
  ) THEN
    RAISE EXCEPTION 'axes_lifecycle_inconsistent_with_status' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

-- Fires before guard_update (trigger names run alphabetically) so the guard sees the final axes.
CREATE TRIGGER trg_memory_version_axes_dual_write
  BEFORE INSERT OR UPDATE ON memory_version
  FOR EACH ROW
  EXECUTE FUNCTION sync_memory_version_axes();

-- v1 inserts the version before its derivation rows (the provenance check is deferred), so a derived claim is
-- first `asserted` and becomes `inferred` the moment its derivation row exists (V2-D38: "inferred when a
-- derivation row exists"). The UPDATE changes only `lifecycle`, which guard_update permits.
CREATE FUNCTION mark_derived_version_inferred() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE memory_version
     SET lifecycle = 'inferred'
   WHERE id = NEW.derived_version_id
     AND status = 'active'
     AND kind = 'claim'
     AND lifecycle = 'asserted';
  RETURN NULL;
END
$$;

CREATE TRIGGER trg_memory_derivation_mark_inferred
  AFTER INSERT ON memory_derivation
  FOR EACH ROW
  EXECUTE FUNCTION mark_derived_version_inferred();

-- Backfill (M1 + M7): a no-op UPDATE of every row; the dual-write trigger fills the NULL axes. Total by construction:
-- the CASE expressions have no ELSE, and the CHECKs below fail the migration if any value is outside the enums.
UPDATE memory_version SET reinforcement_count = reinforcement_count WHERE kind IS NULL;
-- The UPDATE queues the (initially deferred) provenance trigger once per row, and PostgreSQL refuses ALTER TABLE
-- while trigger events are pending (55006). Fire them now - which also re-verifies provenance of every backfilled
-- row - then restore the trigger's declared mode. Only this trigger is INITIALLY DEFERRED (001_full_schema.sql).
SET CONSTRAINTS trg_memory_version_provenance IMMEDIATE;
SET CONSTRAINTS trg_memory_version_provenance DEFERRED;

ALTER TABLE memory_version
  ALTER COLUMN kind SET NOT NULL,
  ALTER COLUMN lifecycle SET NOT NULL,
  ALTER COLUMN retention SET NOT NULL,
  ADD CONSTRAINT memory_version_kind_check CHECK (kind IN ('episode', 'claim', 'procedure', 'abstraction')),
  ADD CONSTRAINT memory_version_lifecycle_check
    CHECK (lifecycle IN ('observed', 'asserted', 'inferred', 'contested', 'retired')),
  ADD CONSTRAINT memory_version_retention_check CHECK (retention IN ('working', 'session', 'durable'));

CREATE OR REPLACE FUNCTION guard_update() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  -- Belief content is immutable in place. The only permitted in-place mutations are
  -- status, archival_branch, the upper bound of recorded_range, and an increment of
  -- reinforcement_count (Technical Specification §5: a duplicate reinforces the
  -- existing version instead of inserting a new one). `kind` is fixed at creation (M1);
  -- `lifecycle` and `retention` move only with `status` (dual-write) until E-016 closes the transitions.
  IF (
       NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.predicate_key, NEW.single_valued,
       NEW.content, NEW.valid_range, NEW.layer, NEW.valid_from_basis, NEW.assertion_mode,
       NEW.confidence, NEW.importance, NEW.extractor_version, NEW.kind
     ) IS DISTINCT FROM (
       OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.predicate_key, OLD.single_valued,
       OLD.content, OLD.valid_range, OLD.layer, OLD.valid_from_basis, OLD.assertion_mode,
       OLD.confidence, OLD.importance, OLD.extractor_version, OLD.kind
     )
  THEN
    RAISE EXCEPTION 'only status, archival_branch, reinforcement_count and the upper bound of recorded_range may change';
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

-- V2-D54 (S-02): every engine function is pinned to `<this schema>, pg_catalog, pg_temp`. CREATE OR REPLACE resets a
-- function's settings, so guard_update is pinned again here along with the two new functions.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['sync_memory_version_axes()', 'mark_derived_version_inferred()', 'guard_update()'] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
