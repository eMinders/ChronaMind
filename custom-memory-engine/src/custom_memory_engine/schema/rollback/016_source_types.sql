-- Rollback for migration 16: restore memory_evidence source_type check to ('turn', 'external').
-- Only safe to apply if no rows with source_type IN ('tool', 'file', 'event') exist.

DO $check$
DECLARE
  bad_count int;
BEGIN
  SELECT count(*) INTO bad_count
  FROM memory_evidence
  WHERE source_type IN ('tool', 'file', 'event');

  IF bad_count > 0 THEN
    RAISE EXCEPTION
      'rollback_016_blocked: % row(s) use source_type tool/file/event; remove them first',
      bad_count;
  END IF;
END
$check$;

ALTER TABLE memory_evidence
  DROP CONSTRAINT IF EXISTS memory_evidence_source_type_check,
  ADD  CONSTRAINT memory_evidence_source_type_check
         CHECK (source_type IN ('turn', 'external'));

DELETE FROM _cme_schema_migrations WHERE version = 16;
