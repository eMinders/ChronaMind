-- Rollback of migration 8 (M4, E-013). Run in ONE transaction by whoever operates the schema; the forward runner never
-- executes it. Restores the version-7 schema exactly (the catalog fingerprint equals the one recorded for version 6).
-- It REFUSES, with a static error, while any non-turn source exists: dropping the columns would lose that evidence.
-- After a re-apply the four new columns sit at later attribute numbers (PostgreSQL never reuses a dropped one); every
-- other catalog property is identical. Roll back later migrations first.

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
