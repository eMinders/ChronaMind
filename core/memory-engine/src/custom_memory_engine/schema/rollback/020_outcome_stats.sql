-- Rollback of migration 20: procedure outcome statistics (E-027).
-- Run in ONE transaction; restores the version-19 schema. Roll back later migrations first.
--
-- Dropping the table drops its trigger, policies and the generated `mean_cost` column with it, and it removes every
-- recorded outcome. That is unavoidable: version 19 has nowhere to keep them, and keeping the data in a table the
-- version-19 engine cannot describe would be a worse answer than losing data this migration introduced.
--
-- `guard_outcome_stats` is dropped explicitly. It is not a table-owned object, so `DROP TABLE` would leave it behind
-- and the version-19 fingerprint comparison would fail on a stray function.

DROP TABLE outcome_stats;
DROP FUNCTION guard_outcome_stats();

-- Every rollback removes its own history row, so `MigrationRunner.status()` still sees a gap-free prefix and reports
-- `pending` rather than `drifted`. Leaving version 20 recorded would make the schema look applied-but-unsupported.
DELETE FROM _cme_schema_migrations WHERE version = 20;
