-- Migration 7: the lifecycle transition table is closed and enforced by the database - E-016, ARCHITECTURE_V2 2.3,
-- V2-D48 (C-P1), V2-D49, I1'.
--
-- A row's `lifecycle` is its epistemic status. If any writer could move an `inferred` guess to `asserted`, or resurrect
-- a retired belief, the fact/hypothesis separation V2 exists for would be gone, so the rules are triggers and CHECKs
-- that hold for every writer (direct SQL under the application role included), not engine conventions.
--
-- 1. KIND-CONDITIONED LEGALITY (V2-D48 C-P1; model.axes.LEGAL_LIFECYCLE). A CHECK, so it also covers rows that
--    never change: an abstraction is never grounded, an episode never adjudicated, a procedure never `observed`.
-- 2. THE CLOSED TRANSITION TABLE (ARCHITECTURE_V2 2.3). Only these lifecycle changes succeed:
--      contested -> asserted | retired      asserted -> retired      observed -> retired
--      inferred  -> retired | contested     (and inferred -> inferred = verification, which changes no column)
--    `retired` is terminal; `inferred -> asserted` is rejected with its own code (I1': promotion needs NEW grounded
--    evidence and therefore a NEW artifact).
-- 3. RECORDS that the current schema can express, checked at commit by the existing deferred provenance trigger:
--      observed | asserted   -> an evidence row           inferred -> a derivation row (I1', derivation half)
--
-- WHAT THIS MIGRATION CANNOT CHECK YET (named, not faked). The method reference of I1', the adjudication record, the
-- verification record and the retirement reason code have no table until E-014 (METHOD, VERIFICATION, ...) and the
-- Adjudicate operations exist. E-014 extends `enforce_lifecycle_records` (CREATE OR REPLACE) in the migration that
-- adds those tables; nothing here pretends to check them.
--
-- DUAL-WRITE WINDOW (E-010). v1 code still drives `status`, and migration 4 derives the lifecycle from it, which forces
-- five narrow, documented allowances that end with the window:
--   * creation as `retired` is allowed only for v1 staging statuses (`candidate`, `rejected`);
--   * a v1 `candidate` row is later promoted to `active` (resolve_conflict_pending), which is a first entry into the
--     lifecycle, not the re-assertion of a retired belief: `retired -> *` is allowed only from `status = 'candidate'`;
--   * a version is inserted before its derivation rows, so `asserted -> inferred` is allowed when - and only when - the
--     row is active and a derivation row exists for it (the artifact IS derived);
--   * accepting a held (`conflict_pending`) candidate that is derived, or a v1 persona, activates it as `inferred`
--     (`contested -> inferred` with the status change to `active`); a plain held claim still needs the
--     `contested -> asserted` adjudication path;
--   * v1 `persona` rows map to abstraction/inferred (V2-D49 D7) but carry evidence, not a derivation row, so they are
--     exempt from the derivation obligation. FINDING for the owner: D7 and I1' disagree for these rows; E-014 should
--     give them a registered method and a zero ledger, after which the exemption is removed.
-- Decision (a) of the issue (does I10 apply to explicit withdrawal?) is unmade; as instructed, (S) or (C) is required for
-- system-initiated retirement only: the archival triggers of migration 1 keep guarding `status = 'archived'`.

ALTER TABLE memory_version
  ADD CONSTRAINT memory_version_lifecycle_legal_for_kind CHECK (
    COALESCE(
      CASE kind
        WHEN 'episode' THEN lifecycle IN ('observed', 'inferred', 'contested', 'retired')
        WHEN 'claim' THEN lifecycle IN ('observed', 'asserted', 'inferred', 'contested', 'retired')
        WHEN 'procedure' THEN lifecycle IN ('asserted', 'inferred', 'contested', 'retired')
        WHEN 'abstraction' THEN lifecycle IN ('inferred', 'contested', 'retired')
      END,
      false
    )
  );

CREATE FUNCTION enforce_lifecycle_transition() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.lifecycle = 'retired' AND NEW.status NOT IN ('candidate', 'rejected') THEN
      RAISE EXCEPTION 'lifecycle_creation_not_permitted' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
  END IF;

  IF NEW.lifecycle IS NOT DISTINCT FROM OLD.lifecycle THEN
    RETURN NEW;
  END IF;

  -- v1 staging promotion (dual-write window): a `candidate` was never part of the lifecycle.
  IF OLD.status = 'candidate' AND OLD.lifecycle = 'retired' THEN
    RETURN NEW;
  END IF;
  IF OLD.lifecycle = 'retired' THEN
    RAISE EXCEPTION 'lifecycle_retired_is_terminal' USING ERRCODE = 'P0001';
  END IF;
  IF OLD.lifecycle = 'inferred' AND NEW.lifecycle = 'asserted' THEN
    RAISE EXCEPTION 'lifecycle_inferred_to_asserted_unrepresentable' USING ERRCODE = 'P0001';
  END IF;
  IF OLD.lifecycle = 'asserted' AND NEW.lifecycle = 'inferred'
     AND NEW.status = 'active' AND EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
    RETURN NEW;  -- dual-write: the version row precedes its derivation row (see header)
  END IF;
  IF OLD.lifecycle = 'contested' AND NEW.lifecycle = 'inferred'
     AND OLD.status = 'conflict_pending' AND NEW.status = 'active' THEN
    RETURN NEW;  -- dual-write: accepting a held candidate that is derived (or a v1 persona) yields an inference
  END IF;
  IF NOT (
    (OLD.lifecycle = 'contested' AND NEW.lifecycle IN ('asserted', 'retired'))
    OR (OLD.lifecycle IN ('asserted', 'observed', 'inferred') AND NEW.lifecycle = 'retired')
    OR (OLD.lifecycle = 'inferred' AND NEW.lifecycle = 'contested')
  ) THEN
    RAISE EXCEPTION 'lifecycle_transition_not_permitted' USING ERRCODE = 'P0001';
  END IF;
  IF NOT COALESCE(
    CASE NEW.kind
      WHEN 'episode' THEN NEW.lifecycle IN ('observed', 'inferred', 'contested', 'retired')
      WHEN 'claim' THEN NEW.lifecycle IN ('observed', 'asserted', 'inferred', 'contested', 'retired')
      WHEN 'procedure' THEN NEW.lifecycle IN ('asserted', 'inferred', 'contested', 'retired')
      WHEN 'abstraction' THEN NEW.lifecycle IN ('inferred', 'contested', 'retired')
    END,
    false
  ) THEN
    RAISE EXCEPTION 'lifecycle_not_legal_for_kind' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

-- Fires after sync_memory_version_axes (migration 5) and guard_update (migration 1) by trigger-name order, so it sees
-- the final column values.
CREATE TRIGGER trg_memory_version_lifecycle_guard
  BEFORE INSERT OR UPDATE ON memory_version
  FOR EACH ROW
  EXECUTE FUNCTION enforce_lifecycle_transition();

-- The record obligations extend v1 Invariant 1 in place: `enforce_provenance` already runs as a deferred constraint
-- trigger on every insert/update of memory_version (trg_memory_version_provenance), so its lifecycle obligations share
-- its trigger - and therefore its enablement - instead of adding a second one. (The frozen design-check script
-- bitemporal_on_full.sql disables exactly that trigger to insert bare versions; one trigger keeps it working.)
CREATE OR REPLACE FUNCTION enforce_provenance() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_status text;
  current_lifecycle text;
  current_layer text;
BEGIN
  IF NEW.status IN ('active', 'archived') THEN
    IF NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'active_or_archived_version_requires_evidence_or_derivation'
        USING ERRCODE = 'P0001';
    END IF;
  END IF;
  -- A deferred trigger sees the row as it was when the event was queued, but the row can change before commit (a
  -- derived claim is inserted `asserted` and becomes `inferred` when its derivation row arrives). Judge what is
  -- committed: re-read the current row, and stop if it is gone.
  SELECT status, lifecycle, layer INTO current_status, current_lifecycle, current_layer
    FROM memory_version WHERE id = NEW.id;
  IF FOUND AND current_status = 'active' THEN
    IF current_lifecycle IN ('observed', 'asserted')
       AND NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id) THEN
      RAISE EXCEPTION 'lifecycle_requires_evidence_record' USING ERRCODE = 'P0001';
    END IF;
    -- I1': inferred => a derivation row (the method half arrives with E-014). v1 `persona` rows are the one inferred
    -- shape v1 can write without a derivation (they carry evidence), so they are exempt for the dual-write window.
    IF current_lifecycle = 'inferred' AND current_layer <> 'persona'
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'inferred_requires_derivation_record' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

-- V2-D54 (S-02): engine functions are pinned to `<this schema>, pg_catalog, pg_temp`. CREATE OR REPLACE resets a
-- function's settings, so enforce_provenance is pinned again along with the new function.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['enforce_lifecycle_transition()', 'enforce_provenance()'] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
