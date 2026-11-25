-- Rollback of migration 27: the entity erasure path.
-- Run in ONE transaction; restores the version-26 schema. Roll back later migrations first.
--
-- Lossless: the migration adds one function and touches no row. After a downgrade, deletions leave orphaned
-- entities in place again -- the pre-27 behaviour -- and nothing that was erased comes back.
DROP FUNCTION IF EXISTS delete_orphaned_entities(text, text[]);

DELETE FROM _cme_schema_migrations WHERE version = 27;
