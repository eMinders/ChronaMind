-- Rollback of migration 12 (E-039). Run in ONE transaction by whoever operates the schema; the forward runner never
-- executes it. Restores the version-11 schema exactly (the catalog fingerprint equals the one recorded for version 11).
-- Dropping the table discards the idempotency records (a replay after this would repeat its effect), and dropping the columns
-- discards the recorded creators. After a re-apply the two columns sit at later attribute numbers. Roll back later migrations first.

DROP TRIGGER IF EXISTS trg_memory_version_creator_fixed ON memory_version;
DROP FUNCTION IF EXISTS guard_creator_fixed();
DROP TABLE api_idempotency;
ALTER TABLE memory_version DROP CONSTRAINT memory_version_creator_check;
ALTER TABLE memory_version DROP COLUMN created_by, DROP COLUMN created_tier;
DELETE FROM _cme_schema_migrations WHERE version = 12;
