-- Migration 19: an entity subject is a foreign key, not a string that looks like one (E-024 §10 row 10, V2-D74).
--
-- V2-D71 let `assert` name the entity a claim is about through an optional `subject_entity`, and stored that id in
-- `memory_version.subject_key`. The handler refuses an entity of another scope and an entity the caller cannot read,
-- so the product path is correct. The schema, however, could not tell an entity id from the literal 'self' that every
-- v1 claim carries: no foreign key, so a claim could name a deleted entity, and the same-scope rule existed only in
-- Python. Anything that writes a version without going through the handler -- a replay harness, a backfill, a future
-- operator -- would have had to reimplement that rule to stay correct.
--
-- This migration makes both properties structural:
--
-- 1. `subject_entity_id` is a typed column: NULL means the subject is `subject_key` itself ('self' today), and a
--    value means the subject IS that entity. `subject_key` keeps holding the same id, because it is the key the
--    `no_bitemporal_overlap` exclusion and the supersession rules already compare (migrations 1 and 15).
-- 2. Entity existence and same-scope are one composite foreign key into `entity`: the referenced row must match on
--    `(id, user_id, scope_path)`, so a claim cannot name an entity of another scope or another principal, and cannot
--    outlive the entity it names (ON DELETE RESTRICT -- a cited entity is not silently removed from a claim).
-- 3. `guard_update` is replaced so the new column is immutable in place, like every other belief column.
--
-- Nothing is backfilled: no row written before this migration recorded an entity subject, and inferring one from
-- `subject_key` would fabricate a link the caller never made.

ALTER TABLE memory_version ADD COLUMN subject_entity_id text;

-- The composite target of the foreign key. `entity` already has `UNIQUE (id, user_id)` (migration 10); the scope is
-- part of the referenced key because that is the property being enforced.
ALTER TABLE entity ADD CONSTRAINT entity_id_user_id_scope_key UNIQUE (id, user_id, scope_path);

ALTER TABLE memory_version
  ADD CONSTRAINT memory_version_subject_entity_scope_fk
    FOREIGN KEY (subject_entity_id, user_id, scope_path)
    REFERENCES entity (id, user_id, scope_path) ON DELETE RESTRICT,
  -- A row that names an entity names it as its subject; the two spellings cannot disagree.
  ADD CONSTRAINT memory_version_subject_entity_is_subject
    CHECK (subject_entity_id IS NULL OR subject_entity_id = subject_key);

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

-- `CREATE OR REPLACE FUNCTION` resets the function's `search_path`, which is a security property here (migration 4
-- pins every engine function so a caller's `search_path` cannot redirect a name the body resolves). Re-pin it.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_update() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;
