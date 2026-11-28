-- Rollback of migration 8 (M4, E-013). Run in ONE transaction by whoever operates the schema; the forward runner never
-- executes it. Restores the version-7 schema exactly (the catalog fingerprint equals the one recorded for version 6).
-- It REFUSES, with a static error, while any non-turn source exists: dropping the columns would lose that evidence.
-- After a re-apply the four new columns sit at later attribute numbers (PostgreSQL never reuses a dropped one); every
-- other catalog property is identical. Roll back later migrations first.
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

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM memory_evidence WHERE source_type <> 'turn') THEN
    RAISE EXCEPTION 'm4_rollback_refused_non_turn_sources_exist' USING ERRCODE = 'P0001';
  END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_memory_evidence_source_dual_write ON memory_evidence;
DROP FUNCTION IF EXISTS sync_memory_evidence_source();
ALTER TABLE memory_evidence
  DROP CONSTRAINT memory_evidence_turn_source_check,
  DROP CONSTRAINT memory_evidence_source_nonblank_check,
  DROP CONSTRAINT memory_evidence_content_hash_check,
  DROP CONSTRAINT memory_evidence_source_type_check,
  DROP CONSTRAINT memory_evidence_pkey;
ALTER TABLE memory_evidence ALTER COLUMN turn_id SET NOT NULL;
ALTER TABLE memory_evidence ADD CONSTRAINT memory_evidence_pkey PRIMARY KEY (version_id, turn_id, evidence_span);
ALTER TABLE memory_evidence
  DROP COLUMN content_hash,
  DROP COLUMN span,
  DROP COLUMN source_ref,
  DROP COLUMN source_type;
DELETE FROM _cme_schema_migrations WHERE version = 8;
