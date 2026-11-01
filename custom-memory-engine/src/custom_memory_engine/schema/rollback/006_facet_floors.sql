-- Rollback of migration 6 (M8, E-020). Run in ONE transaction by whoever operates the schema; the forward runner never
-- executes it. Restores the version-5 schema exactly (the catalog fingerprint equals the one recorded for version 4).
-- Dropping the column discards any `um_facet` tags written since M8 - they are the only data it holds.
-- Roll back later migrations first. After a re-apply the column sits at a later attribute number (PostgreSQL never
-- reuses a dropped one); every other catalog property is identical.

DROP INDEX IF EXISTS memory_version_um_facet_idx;
ALTER TABLE memory_version
  DROP CONSTRAINT memory_version_retention_floor,
  DROP CONSTRAINT memory_version_um_facet_check;
ALTER TABLE memory_version DROP COLUMN um_facet;
DELETE FROM _cme_schema_migrations WHERE version = 6;
