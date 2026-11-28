-- Migration 24: an open-ended semantic slot, an explicit cardinality hint, and source authority (W3.5, V2-D87).
--
-- Three columns, one idea: the database stops being the place where *domain* vocabulary is decided, and stays the
-- place where *invariants* are enforced.
--
-- 1. `semantic_slot` -- an open-ended, caller-supplied grouping key ("residence", "meeting_time_constraint",
--    "preferred_editor", ...). It is suggested by semantic extraction, not looked up in a catalog, so a domain nobody
--    anticipated needs no migration. `predicate_key` remains a foreign key into `predicate_schema` because the closed
--    *structural* vocabulary (`episode`, `statement`, `plans_to`) is exactly what a foreign key should protect; the
--    open *domain* vocabulary lives here. V1's mistake was not having a catalog -- it was making the catalog the only
--    way to say what a claim is about, which is what forced V2 `assert` to write `predicate_key = 'statement'` for
--    every claim and thereby switch conflict detection off entirely.
--
-- 2. `cardinality_hint` -- the extraction layer's contextual judgement (`single`, `multi`, `event`), recorded so the
--    decision that produced `single_valued` is auditable rather than invisible. It is a *hint*: the conflict pipeline
--    is what decides whether a claim actually supersedes another. Storing it separately from `single_valued` keeps a
--    judgement and its consequence distinguishable after the fact.
--
-- 3. `memory_evidence.authority_rank` -- a **generated** column, not a caller-supplied one: 0 user, 1 system,
--    2 assistant, 3 third party (external/tool/file/event, or an unrecognised role). Veracium's authority ladder
--    (arXiv:2607.21962) is only a guarantee if it cannot be asserted by the text it governs, and a generated column
--    is the only form a compromised runtime cannot forge. It is derived from `source_type` and `evidence_role`, both
--    of which migration 8/17 already make structural.
--
-- The exclusion key gains the slot. `no_bitemporal_overlap` compares `(scope_path, subject_key, predicate_key)` as
-- migration 15 (I5) re-keyed it; with one generic predicate for every claim, two unrelated single-valued facts about
-- the same subject (`residence` and `preferred_editor`) collide on the same key and the constraint would refuse a legal
-- write. The key becomes `COALESCE(semantic_slot, predicate_key)`, which is *the same key as before* for every row
-- that names no slot (all 22 migrations of existing data) and becomes slot-scoped only where a slot was actually
-- supplied. `COALESCE` is immutable and `btree_gist` indexes text expressions, so this is an exclusion constraint and
-- not merely a comment.
--
-- Additive in every other respect: no row is rewritten, no column changes type, no constraint is dropped that is not
-- immediately re-added with a strictly wider key.

-- 1. Open-ended semantic slot ------------------------------------------------------------------------------------
ALTER TABLE memory_version
  ADD COLUMN semantic_slot text,
  ADD COLUMN cardinality_hint text,
  ADD CONSTRAINT memory_version_semantic_slot_check CHECK (
    semantic_slot IS NULL
    OR (
      semantic_slot = btrim(semantic_slot)
      AND length(semantic_slot) BETWEEN 1 AND 120
      AND semantic_slot !~ '[[:cntrl:]]'
    )
  ),
  ADD CONSTRAINT memory_version_cardinality_hint_check CHECK (
    cardinality_hint IS NULL OR cardinality_hint IN ('single', 'multi', 'event')
  );

-- Slot-scoped reads: "what does this user's model say about `residence`" is a scope+facet query, mirroring the
-- existing `um_facet` index (migration 15 recreates it as `(scope_path text_pattern_ops, um_facet)`).
CREATE INDEX memory_version_semantic_slot_idx ON memory_version (scope_path text_pattern_ops, semantic_slot)
  WHERE semantic_slot IS NOT NULL;

-- 2. Slot-scoped exclusion key (I5, as migration 15 re-keyed it) -------------------------------------------------
--
-- `scope_path`, not `user_id`: migration 15 moved the key to the scope, so two distinct scopes of ONE user may each
-- hold the same single-valued predicate. Restating the key as `user_id` here would silently re-tighten `I5` for every
-- sub-scope and refuse the second write.
ALTER TABLE memory_version DROP CONSTRAINT no_bitemporal_overlap;
ALTER TABLE memory_version ADD CONSTRAINT no_bitemporal_overlap EXCLUDE USING gist (
  scope_path WITH =,
  subject_key WITH =,
  (COALESCE(semantic_slot, predicate_key)) WITH =,
  event_range WITH &&,
  recorded_range WITH &&
) WHERE (single_valued AND status IN ('active', 'archived'));

-- 3. Source authority, derived and unforgeable -------------------------------------------------------------------
ALTER TABLE memory_evidence ADD COLUMN authority_rank smallint GENERATED ALWAYS AS (
  CASE
    WHEN source_type <> 'turn' THEN 3          -- external, tool, file, event: third-party authority
    WHEN evidence_role = 'user' THEN 0
    WHEN evidence_role = 'system' THEN 1
    WHEN evidence_role = 'assistant' THEN 2
    ELSE 3                                      -- an unrecognised role is untrusted, not trusted-by-default
  END
) STORED;

-- 4. `guard_update`: the two new belief columns are immutable in place -------------------------------------------------
--
-- Reproduced in full (migration 22's body, plus `semantic_slot` and `cardinality_hint` in both tuples). A partial
-- replacement would silently drop the V2-D50 tombstone branch, so the whole function is restated rather than patched.
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
  -- `semantic_slot` and `cardinality_hint` (migration 24) are belief columns like `subject_key`: re-pointing a claim
  -- at another slot would rewrite what the claim is about, and the exclusion key and the supersession query both
  -- compare it.
  IF is_source_deleted_tombstone THEN
    IF (
         NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.subject_entity_id, NEW.predicate_key,
         NEW.semantic_slot, NEW.cardinality_hint,
         NEW.single_valued, NEW.event_range, NEW.layer, NEW.valid_from_basis, NEW.assertion_mode,
         NEW.confidence, NEW.importance, NEW.extractor_version, NEW.kind
       ) IS DISTINCT FROM (
         OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.subject_entity_id, OLD.predicate_key,
         OLD.semantic_slot, OLD.cardinality_hint,
         OLD.single_valued, OLD.event_range, OLD.layer, OLD.valid_from_basis, OLD.assertion_mode,
         OLD.confidence, OLD.importance, OLD.extractor_version, OLD.kind
       )
    THEN
      RAISE EXCEPTION 'only status, archival_branch, reinforcement_count and the upper bound of recorded_range may change';
    END IF;
  ELSE
    IF (
         NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.subject_entity_id, NEW.predicate_key,
         NEW.semantic_slot, NEW.cardinality_hint,
         NEW.single_valued, NEW.content, NEW.event_range, NEW.layer, NEW.valid_from_basis, NEW.assertion_mode,
         NEW.confidence, NEW.importance, NEW.extractor_version, NEW.kind, NEW.retirement_reason
       ) IS DISTINCT FROM (
         OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.subject_entity_id, OLD.predicate_key,
         OLD.semantic_slot, OLD.cardinality_hint,
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

-- `CREATE OR REPLACE FUNCTION` resets the function's `search_path`, which is a security property here (migration 4
-- pins every engine function). Re-pin it (V2-D54).
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_update() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;

-- 5. Postconditions ---------------------------------------------------------------------------------------------
DO $verify$
DECLARE
  overlap_def text;
BEGIN
  SELECT pg_catalog.pg_get_constraintdef(oid) INTO overlap_def
  FROM pg_catalog.pg_constraint
  WHERE conrelid = 'memory_version'::regclass AND conname = 'no_bitemporal_overlap';

  IF overlap_def IS NULL OR overlap_def NOT LIKE '%COALESCE(semantic_slot, predicate_key)%' THEN
    -- A key that still ignores the slot would refuse a legal second single-valued fact about the same subject.
    RAISE EXCEPTION 'migration_024_postcondition_failed: exclusion key does not carry the semantic slot';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_attribute
    WHERE attrelid = 'memory_evidence'::regclass AND attname = 'authority_rank' AND attgenerated = 's'
  ) THEN
    RAISE EXCEPTION 'migration_024_postcondition_failed: authority_rank is not a stored generated column';
  END IF;
END
$verify$;
