-- Rollback of migration 14 (E-016 entry records). Run in ONE transaction by whoever operates the schema; the forward
-- runner never executes it. Restores the version-13 schema exactly (the catalog fingerprint equals the one recorded for
-- version 13). Nothing is stored by migration 14, so nothing is discarded. Roll back later migrations first.

DROP TRIGGER IF EXISTS trg_memory_version_record_of_entry ON memory_version;
DROP TRIGGER IF EXISTS trg_memory_version_record_of_creation ON memory_version;
DROP FUNCTION IF EXISTS enforce_entry_records();
DELETE FROM _cme_schema_migrations WHERE version = 14;
