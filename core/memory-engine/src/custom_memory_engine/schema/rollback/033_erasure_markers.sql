-- Rollback of migration 33: erasure markers and the restore-reconciliation identity.
-- Run in ONE transaction; restores the version-32 schema. The markers go with their table; a journal written by the
-- version-33 code is format 2, which version-32 code refuses (`journal_format_unsupported`) rather than half-applies.

DROP TABLE restore_reconciliation;
DROP INDEX api_idempotency_result;
DROP TABLE erased_turn;
DROP TABLE erasure_marker;
ALTER TABLE deletion_request DROP CONSTRAINT deletion_request_id_user_id_key;

DELETE FROM _cme_schema_migrations WHERE version = 33;
