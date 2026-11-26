-- Rollback for migration 16: restore memory_evidence source_type check to ('turn', 'external').
-- Only safe to apply if no rows with source_type IN ('tool', 'file', 'event') exist.
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
