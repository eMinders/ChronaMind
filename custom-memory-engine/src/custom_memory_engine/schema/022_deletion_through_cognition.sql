-- Migration 22: deletion through cognition (E-037; V2-D09, V2-D50, T-29/S-08, DELETION_ORDER.md F9).
--
-- 1. Acyclic derivation graph at write time (F9): ``prevent_derivation_cycle()`` rejects any insert or update on
--    ``memory_derivation`` that would close a cycle of any length, using a cycle-safe recursive query.
-- 2. Retire-by-deletion tombstoning (V2-D50 rule 3): adds ``memory_version.retirement_reason`` (currently only
--    ``'source_deleted'``), permits clearing ``content`` to ``''`` in ``guard_update()`` **solely** during that
--    tombstone transition, recognizes ``retirement_reason = 'source_deleted'`` as a recorded retirement reason in
--    ``enforce_transition_records()``, and permits a tombstoned row to hold zero derivation/evidence rows in
--    ``enforce_provenance()``.
-- 3. Narrowed privilege paths (S-08 & V2-D50 rule 3): ``delete_exclusive_turns(text, text[])`` and
--    ``clear_retired_tombstone_vectors(text, text[])`` are ``SECURITY DEFINER`` functions with pinned ``search_path``
--    that verify ``app.user_id`` and ``app.deletion_active = '1'``, widening ``DELETE`` on ``turn`` and
--    ``memory_embedding`` **only** inside a deletion transaction without widening table grants for the runtime role.
--
-- No backfill is performed or invented: existing rows carry ``retirement_reason IS NULL``.

ALTER TABLE memory_version
  ADD COLUMN retirement_reason text CHECK (retirement_reason IS NULL OR retirement_reason IN ('source_deleted'));

-- 1. Acyclic derivation graph (F9) ----------------------------------------------------------------------------------
CREATE FUNCTION prevent_derivation_cycle() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  creates_cycle boolean;
BEGIN
  IF NEW.derived_version_id = NEW.source_version_id THEN
    RAISE EXCEPTION 'derivation_cycle_forbidden' USING ERRCODE = 'P0001';
  END IF;
  WITH RECURSIVE ancestors(version_id) AS (
    SELECT d.source_version_id
      FROM memory_derivation AS d
     WHERE d.user_id = NEW.user_id AND d.derived_version_id = NEW.source_version_id
    UNION
    SELECT d.source_version_id
      FROM memory_derivation AS d
      JOIN ancestors AS a ON d.derived_version_id = a.version_id
     WHERE d.user_id = NEW.user_id
  )
  SELECT EXISTS (SELECT 1 FROM ancestors WHERE version_id = NEW.derived_version_id)
    INTO creates_cycle;
  IF creates_cycle THEN
    RAISE EXCEPTION 'derivation_cycle_forbidden' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_memory_derivation_acyclic
  BEFORE INSERT OR UPDATE OF derived_version_id, source_version_id ON memory_derivation
  FOR EACH ROW EXECUTE FUNCTION prevent_derivation_cycle();

-- 2. Retire-by-deletion tombstoning in guard_update / enforce_transition_records / enforce_provenance ---------------
CREATE OR REPLACE FUNCTION guard_update() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  is_source_deleted_tombstone boolean;
BEGIN
  -- Belief content is immutable in place, with one narrow V2-D50 exception: retiring a dependent by source deletion
  -- tombstones its content to '' and records retirement_reason = 'source_deleted' in the same transaction.
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

CREATE OR REPLACE FUNCTION enforce_provenance() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_status text;
  current_lifecycle text;
  current_layer text;
  current_kind text;
  current_method text;
  current_retirement_reason text;
BEGIN
  -- Judge what is committed: re-read the current row, and stop if it was deleted before commit.
  SELECT status, lifecycle, layer, kind, method_id, retirement_reason
    INTO current_status, current_lifecycle, current_layer, current_kind, current_method, current_retirement_reason
    FROM memory_version WHERE id = NEW.id;
  IF NOT FOUND THEN
    RETURN NEW;
  END IF;
  IF current_status IN ('active', 'archived')
     AND NOT (current_lifecycle = 'retired' AND current_retirement_reason = 'source_deleted') THEN
    IF NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'active_or_archived_version_requires_evidence_or_derivation'
        USING ERRCODE = 'P0001';
    END IF;
  END IF;
  IF current_status = 'active' THEN
    IF current_lifecycle IN ('observed', 'asserted')
       AND NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id) THEN
      RAISE EXCEPTION 'lifecycle_requires_evidence_record' USING ERRCODE = 'P0001';
    END IF;
    IF current_lifecycle = 'inferred' THEN
      IF current_layer <> 'persona'
         AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
        RAISE EXCEPTION 'inferred_requires_derivation_record' USING ERRCODE = 'P0001';
      END IF;
      IF current_method IS NULL THEN
        RAISE EXCEPTION 'inferred_requires_method_record' USING ERRCODE = 'P0001';
      END IF;
    END IF;
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
  current_retirement_reason text;
BEGIN
  SELECT lifecycle, status, recorded_range, archival_branch, retirement_reason
    INTO current_lifecycle, current_status, current_range, current_branch, current_retirement_reason
    FROM memory_version WHERE id = NEW.id;
  IF NOT FOUND OR current_lifecycle IS DISTINCT FROM NEW.lifecycle THEN
    RETURN NULL;
  END IF;
  IF (OLD.status = 'candidate' AND OLD.lifecycle = 'retired') OR NEW.lifecycle = 'inferred' THEN
    RETURN NULL;
  END IF;
  -- V2-D50 rule 3: retire-by-deletion records its reason on the tombstoned row.
  IF current_lifecycle = 'retired' AND current_retirement_reason = 'source_deleted' THEN
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

-- 3. Narrowed SECURITY DEFINER deletion helpers (S-08 & V2-D50 rule 3) ----------------------------------------------
CREATE FUNCTION delete_exclusive_turns(p_user_id text, p_turn_ids text[]) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  deleted_count integer := 0;
BEGIN
  IF NULLIF(pg_catalog.current_setting('app.user_id', true), '') IS DISTINCT FROM p_user_id
     OR pg_catalog.current_setting('app.deletion_active', true) IS DISTINCT FROM '1' THEN
    RAISE EXCEPTION 'exclusive_turn_deletion_forbidden' USING ERRCODE = '42501';
  END IF;
  DELETE FROM turn AS t
   WHERE t.user_id = p_user_id
     AND t.id = ANY(p_turn_ids)
     AND NOT EXISTS (
       SELECT 1 FROM memory_evidence AS e WHERE e.user_id = p_user_id AND e.turn_id = t.id
     )
     AND NOT EXISTS (
       SELECT 1 FROM job AS j WHERE j.user_id = p_user_id AND j.turn_id = t.id
     );
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END
$$;

CREATE FUNCTION clear_retired_tombstone_vectors(p_user_id text, p_version_ids text[]) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  deleted_count integer := 0;
BEGIN
  IF NULLIF(pg_catalog.current_setting('app.user_id', true), '') IS DISTINCT FROM p_user_id
     OR pg_catalog.current_setting('app.deletion_active', true) IS DISTINCT FROM '1' THEN
    RAISE EXCEPTION 'tombstone_vector_cleanup_forbidden' USING ERRCODE = '42501';
  END IF;
  DELETE FROM memory_embedding AS e
   WHERE e.user_id = p_user_id
     AND e.version_id = ANY(p_version_ids)
     AND EXISTS (
       SELECT 1 FROM memory_version AS v
        WHERE v.user_id = p_user_id
          AND v.id = e.version_id
          AND v.lifecycle = 'retired'
          AND v.retirement_reason = 'source_deleted'
     );
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END
$$;

-- Pin search_path on every new and replaced engine function (V2-D54 S-02).
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'prevent_derivation_cycle()',
    'guard_update()',
    'enforce_provenance()',
    'enforce_transition_records()',
    'delete_exclusive_turns(text, text[])',
    'clear_retired_tombstone_vectors(text, text[])'
  ] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
