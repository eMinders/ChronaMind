-- Rollback of migration 22: deletion through cognition (E-037).
-- Run in ONE transaction; restores the version-21 schema byte-for-byte. Roll back later migrations first.

DROP TRIGGER IF EXISTS trg_memory_derivation_acyclic ON memory_derivation;
DROP FUNCTION IF EXISTS prevent_derivation_cycle();
DROP FUNCTION IF EXISTS delete_exclusive_turns(text, text[]);
DROP FUNCTION IF EXISTS clear_retired_tombstone_content(text, text[]);
DROP POLICY IF EXISTS procedure_shape_tombstone ON procedure_shape;

CREATE OR REPLACE FUNCTION guard_update() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  -- Belief content is immutable in place. The only permitted in-place mutations are
  -- status, archival_branch, the upper bound of recorded_range, and an increment of
  -- reinforcement_count (Technical Specification §5: a duplicate reinforces the
  -- existing version instead of inserting a new one). `kind` is fixed at creation (M1);
  -- `lifecycle` and `retention` move only with `status` (dual-write) until E-016 closes the transitions.
  -- `subject_entity_id` (migration 19) is a belief column like `subject_key`: re-pointing a claim at another entity
  -- would rewrite what the claim is about.
  IF (
       NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.subject_entity_id, NEW.predicate_key,
       NEW.single_valued, NEW.content, NEW.event_range, NEW.layer, NEW.valid_from_basis, NEW.assertion_mode,
       NEW.confidence, NEW.importance, NEW.extractor_version, NEW.kind
     ) IS DISTINCT FROM (
       OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.subject_entity_id, OLD.predicate_key,
       OLD.single_valued, OLD.content, OLD.event_range, OLD.layer, OLD.valid_from_basis, OLD.assertion_mode,
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

CREATE OR REPLACE FUNCTION enforce_provenance() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_status text;
  current_lifecycle text;
  current_layer text;
  current_kind text;
  current_method text;
BEGIN
  IF NEW.status IN ('active', 'archived') THEN
    IF NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'active_or_archived_version_requires_evidence_or_derivation'
        USING ERRCODE = 'P0001';
    END IF;
  END IF;
  -- A deferred trigger sees the row as it was when the event was queued, but the row can change before commit.
  -- Judge what is committed: re-read the current row, and stop if it is gone.
  SELECT status, lifecycle, layer, kind, method_id
    INTO current_status, current_lifecycle, current_layer, current_kind, current_method
    FROM memory_version WHERE id = NEW.id;
  IF FOUND AND current_status = 'active' THEN
    IF current_lifecycle IN ('observed', 'asserted')
       AND NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id) THEN
      RAISE EXCEPTION 'lifecycle_requires_evidence_record' USING ERRCODE = 'P0001';
    END IF;
    -- I1': inferred => a derivation row AND a registered method (I3). v1 persona rows carry evidence, not a derivation
    -- row, so they are exempt from the derivation half for the dual-write window; they do carry the persona method.
    IF current_lifecycle = 'inferred' THEN
      IF current_layer <> 'persona'
         AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
        RAISE EXCEPTION 'inferred_requires_derivation_record' USING ERRCODE = 'P0001';
      END IF;
      IF current_method IS NULL THEN
        RAISE EXCEPTION 'inferred_requires_method_record' USING ERRCODE = 'P0001';
      END IF;
    END IF;
    -- I15: an abstraction exists only with its ledger.
    IF current_kind = 'abstraction'
       AND NOT EXISTS (SELECT 1 FROM abstraction_stats WHERE version_id = NEW.id) THEN
      RAISE EXCEPTION 'abstraction_requires_a_ledger' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION enforce_transition_records() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_lifecycle text;
  current_status text;
  current_range tstzrange;
  current_branch text;
BEGIN
  -- Judge what is committed; a row deleted in the same transaction (the deletion path) has nothing to record.
  SELECT lifecycle, status, recorded_range, archival_branch
    INTO current_lifecycle, current_status, current_range, current_branch
    FROM memory_version WHERE id = NEW.id;
  IF NOT FOUND OR current_lifecycle IS DISTINCT FROM NEW.lifecycle THEN
    RETURN NULL;
  END IF;
  -- A v1 staged candidate entering the lifecycle, and the dual-write corrections to `inferred`, record nothing.
  IF (OLD.status = 'candidate' AND OLD.lifecycle = 'retired') OR NEW.lifecycle = 'inferred' THEN
    RETURN NULL;
  END IF;

  IF OLD.lifecycle = 'contested' AND NEW.lifecycle = 'asserted' THEN
    IF NOT EXISTS (SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND decision = 'accept') THEN
      RAISE EXCEPTION 'lifecycle_requires_adjudication_record' USING ERRCODE = 'P0001';
    END IF;
  ELSIF OLD.lifecycle = 'contested' AND NEW.lifecycle = 'retired' THEN
    IF NOT EXISTS (SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND decision = 'reject') THEN
      RAISE EXCEPTION 'lifecycle_requires_reason_code' USING ERRCODE = 'P0001';
    END IF;
  ELSIF OLD.lifecycle IN ('asserted', 'observed') AND NEW.lifecycle = 'retired' THEN
    -- withdrawal or supersession closes recorded time; the v1 archival path records its branch
    IF upper_inf(current_range) AND current_branch IS NULL THEN
      RAISE EXCEPTION 'lifecycle_requires_recorded_time_close' USING ERRCODE = 'P0001';
    END IF;
  ELSIF OLD.lifecycle = 'inferred' AND NEW.lifecycle = 'retired' THEN
    IF (current_range IS NULL OR upper_inf(current_range)) AND current_branch IS NULL
       AND NOT EXISTS (
         SELECT 1 FROM verification WHERE version_id = NEW.id AND user_id = NEW.user_id AND outcome = 'refuted'
       )
       AND NOT EXISTS (
         SELECT 1 FROM negative_constraint WHERE dissented_version_id = NEW.id AND user_id = NEW.user_id
       )
       AND NOT EXISTS (SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND decision = 'reject') THEN
      RAISE EXCEPTION 'lifecycle_requires_reason_code' USING ERRCODE = 'P0001';
    END IF;
  ELSIF OLD.lifecycle = 'inferred' AND NEW.lifecycle = 'contested' THEN
    -- grounded evidence contradicts it (2.3 rule 1): a Tier C contradicts edge
    IF NOT EXISTS (
      SELECT 1 FROM association
       WHERE user_id = NEW.user_id AND type = 'contradicts' AND origin = 'tier_c'
         AND (from_id = NEW.id OR to_id = NEW.id)
    ) THEN
      RAISE EXCEPTION 'lifecycle_requires_contradiction_record' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NULL;
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['guard_update()', 'enforce_provenance()', 'enforce_transition_records()'] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;

DROP FUNCTION IF EXISTS deletion_in_this_transaction(text);

ALTER TABLE memory_version DROP COLUMN IF EXISTS retirement_reason;

DELETE FROM _cme_schema_migrations WHERE version = 22;
