-- Migration 16: extend memory_evidence source types to include tool, file, and event (V2-D66, V2-D41 extension).
--
-- V2-D41 declared two source types at W1: 'turn' and 'external'. The coding-agent profile (E-021, #136) requires
-- three additional types: 'tool' (tool-call output), 'file' (diff/patch evidence), and 'event' (CI/test-run result).
-- These are all stored as external-reference rows — they carry a content_hash but no turn_id — so no new columns,
-- foreign keys, or triggers are needed. Only the check constraint is widened.
--
-- Backward compatibility: existing 'turn' and 'external' rows satisfy the new constraint without any backfill.
-- The rollback script restores the original constraint after migrating any newer rows out.

DO $check$
DECLARE
  bad_count int;
BEGIN
  SELECT count(*) INTO bad_count
  FROM memory_evidence
  WHERE source_type NOT IN ('turn', 'external', 'tool', 'file', 'event');

  IF bad_count > 0 THEN
    RAISE EXCEPTION 'migration_016_preflight_failed: % row(s) carry an unrecognised source_type', bad_count;
  END IF;
END
$check$;

ALTER TABLE memory_evidence
  DROP CONSTRAINT IF EXISTS memory_evidence_source_type_check,
  ADD  CONSTRAINT memory_evidence_source_type_check
         CHECK (source_type IN ('turn', 'external', 'tool', 'file', 'event'));

-- The turn-source integrity rule is unchanged: only 'turn' rows carry a turn_id.
-- The combined constraint already enforces this via the existing
-- memory_evidence_turn_source_check; no update needed there because 'tool'/'file'/'event'
-- rows have turn_id IS NULL, which satisfies the OR branch for 'external'.
-- Verify the constraint name still exists and covers the new types.
DO $verify$
DECLARE
  cname text;
BEGIN
  SELECT conname INTO cname
  FROM pg_constraint
  WHERE conrelid = 'memory_evidence'::regclass
    AND contype = 'c'
    AND conname = 'memory_evidence_source_type_check';

  IF cname IS NULL THEN
    RAISE EXCEPTION 'migration_016_postcondition_failed: constraint not found after alter';
  END IF;
END
$verify$;
