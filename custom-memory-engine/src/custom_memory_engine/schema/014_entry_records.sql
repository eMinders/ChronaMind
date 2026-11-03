-- Migration 14: the records of entry into the lifecycle - E-016 (#67), ARCHITECTURE_V2 2.3, I1', V2-D61.
--
-- Migrations 7 and 10 enforce the closed transition table and the record of every transition *between* lifecycles.
-- This one closes the creation rows of the same table, which until now were enforced only in part:
--
--      - -> asserted    evidence (migration 7) + a gate decision: an `accept` row in memory_conflict for this version
--      - -> contested   a gate decision: a `hold` row in memory_conflict for this version
--      - -> inferred    a derivation row + a registered method (migrations 7 and 10), now also for a v1 persona row
--      - -> observed    an evidence row (migration 7; its source type is a declared one, migration 8)
--
-- and the two v1 ways into the lifecycle that are not creations, which would otherwise route around them:
--
--      staged candidate (`candidate`, retired) -> asserted | inferred   an `accept` decision (the R4 promotion record)
--      staged candidate                        -> contested             a `hold` decision
--      contested -> inferred (held derived/persona accepted)            an `accept` decision + a derivation row
--
-- A RE-RECORD IS NOT A NEW ASSERTION. Close-then-insert (R1-R3, E3, R7) closes a believed version at tick `c` and inserts
-- a copy of it at `[c, NULL)` with a truncated or re-bounded event range. That copy asserts nothing the gate has not
-- already decided, so it carries the decision of the belief it continues: it is accepted without a decision of its own
-- when a version with the same memory key, subject, predicate, kind and content was closed at exactly `c`, and its event
-- range lies inside what was believed for that subject and predicate until `c`. The ablations that DELETE the closed
-- version instead (A2, A2b) leave nothing to continue; under the application role they are refused (they are
-- evaluation-only, run by the operator).
--
-- WHO IS BOUND (V2-D61). Every role that is not a superuser, i.e. the application role that #67 names as the boundary.
-- A superuser can disable any trigger and bypasses row security, so no trigger binds it; the v1 test and evaluation
-- harnesses connect as one and commit claims directly, without the gate, and the v1 archival and immutability tests must
-- pass unchanged. Every engine write path the application role reaches (engine.ingest.apply_candidate, the API) already
-- records the decision in the same transaction, which is all this checks: the check is deferred to commit.
--
-- THE v1 PERSONA EXEMPTION (migration 7) is narrowed by the same rule: a persona row that the application role creates
-- or brings into the lifecycle needs a derivation row like any other inference. Persona rows that already exist, and
-- later writes to them, keep the exemption, so the dual-write window and the D7 backfill are unaffected.

CREATE FUNCTION enforce_entry_records() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_lifecycle text;
  current_range tstzrange;
  entered text;
BEGIN
  IF (SELECT r.rolsuper FROM pg_catalog.pg_roles r WHERE r.rolname = current_user) THEN
    RETURN NULL;
  END IF;
  -- Judge what is committed; a row deleted in the same transaction (the deletion path) entered nothing.
  SELECT lifecycle, recorded_range INTO current_lifecycle, current_range FROM memory_version WHERE id = NEW.id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  entered := NEW.lifecycle;
  -- A derived claim is inserted `asserted` and becomes `inferred` when its derivation row arrives (migration 5): it
  -- entered the lifecycle as an inference, and the gate never saw it.
  IF entered = 'asserted' AND current_lifecycle = 'inferred' THEN
    entered := 'inferred';
  END IF;

  IF entered = 'inferred'
     AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
    RAISE EXCEPTION 'inferred_requires_derivation_record' USING ERRCODE = 'P0001';
  END IF;

  -- Not a creation: a staged candidate promoted, or a held candidate accepted. Both are adjudications (R4), and
  -- resolve_conflict_pending records them; a candidate that is held instead is held by a gate decision.
  IF TG_OP = 'UPDATE' THEN
    IF entered = 'contested'
       AND NOT EXISTS (
         SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND user_id = NEW.user_id AND decision = 'hold'
       ) THEN
      RAISE EXCEPTION 'lifecycle_requires_gate_decision' USING ERRCODE = 'P0001';
    END IF;
    IF entered IN ('asserted', 'inferred')
       AND NOT EXISTS (
         SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND user_id = NEW.user_id AND decision = 'accept'
       ) THEN
      RAISE EXCEPTION 'lifecycle_requires_adjudication_record' USING ERRCODE = 'P0001';
    END IF;
    RETURN NULL;
  END IF;

  IF entered = 'contested' THEN
    IF NOT EXISTS (
      SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND user_id = NEW.user_id AND decision = 'hold'
    ) THEN
      RAISE EXCEPTION 'lifecycle_requires_gate_decision' USING ERRCODE = 'P0001';
    END IF;
  ELSIF entered = 'asserted' THEN
    IF NOT EXISTS (
         SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND user_id = NEW.user_id AND decision = 'accept'
       )
       AND NOT (
         current_range IS NOT NULL
         -- the belief this row re-records, closed at the instant this one starts
         AND EXISTS (
           SELECT 1 FROM memory_version p
            WHERE p.user_id = NEW.user_id AND p.id <> NEW.id
              AND p.memory_key = NEW.memory_key AND p.subject_key = NEW.subject_key
              AND p.predicate_key = NEW.predicate_key AND p.kind = NEW.kind AND p.content = NEW.content
              AND p.lifecycle = 'asserted'
              AND p.recorded_range IS NOT NULL AND NOT upper_inf(p.recorded_range)
              AND upper(p.recorded_range) = lower(current_range)
         )
         -- and it claims no event time that was not believed for this subject and predicate until then
         AND COALESCE((
           SELECT NEW.event_range <@ range_agg(q.event_range) FROM memory_version q
            WHERE q.user_id = NEW.user_id AND q.id <> NEW.id
              AND q.subject_key = NEW.subject_key AND q.predicate_key = NEW.predicate_key
              AND q.recorded_range IS NOT NULL AND NOT upper_inf(q.recorded_range)
              AND upper(q.recorded_range) = lower(current_range)
         ), false)
       ) THEN
      RAISE EXCEPTION 'lifecycle_requires_gate_decision' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NULL;
END
$$;

-- Both are deferred constraint triggers judged at commit (the gate records its decision after the row is written), so
-- both are listed in engine.deletion.ARCHIVAL_GUARD_TRIGGERS: deletion judges the final state (r10). Triggers on one
-- event fire in name order, so `record_of_*` runs after `trg_memory_version_provenance`: a row with no evidence at all
-- reports Invariant 1, the more basic failure, rather than its missing decision.
CREATE CONSTRAINT TRIGGER trg_memory_version_record_of_creation
  AFTER INSERT ON memory_version
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION enforce_entry_records();

-- Plain AFTER UPDATE with a WHEN clause, not `UPDATE OF lifecycle`: the lifecycle is changed by the dual-write trigger
-- from `status`, and a column-list trigger fires only for columns named in the statement (migration 10).
CREATE CONSTRAINT TRIGGER trg_memory_version_record_of_entry
  AFTER UPDATE ON memory_version
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  WHEN (
    OLD.lifecycle IS DISTINCT FROM NEW.lifecycle
    AND (
      (OLD.status = 'candidate' AND OLD.lifecycle = 'retired')
      OR (OLD.lifecycle = 'contested' AND NEW.lifecycle = 'inferred')
    )
  )
  EXECUTE FUNCTION enforce_entry_records();

-- V2-D54 (S-02): the new function is pinned to the engine schema.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.enforce_entry_records() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;
