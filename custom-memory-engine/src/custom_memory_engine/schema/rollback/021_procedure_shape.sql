-- Rollback of migration 21: procedure storage (E-030).
-- Run in ONE transaction; restores the version-20 schema. Roll back later migrations first.
--
-- DATA LOSS, stated rather than discovered: dropping the table removes every stored `preconditions` and `steps` blob.
-- Version 20 has nowhere to keep them. The procedures themselves (their `memory_version` rows, evidence and
-- `outcome_stats`) are untouched; with no shape they read as "no preconditions", which is "always applicable". Nothing
-- is backfilled on reapply: a procedure written before the reapply simply has no shape.
--
-- `guard_procedure_shape` is not table-owned, so `DROP TABLE` would leave it behind and the version-20 fingerprint
-- comparison would fail on a stray function.

DROP TABLE procedure_shape;
DROP FUNCTION guard_procedure_shape();

-- Every rollback removes its own history row, so `MigrationRunner.status()` still sees a gap-free prefix.
DELETE FROM _cme_schema_migrations WHERE version = 21;
