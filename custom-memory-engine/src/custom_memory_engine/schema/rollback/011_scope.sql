-- Rollback of migration 11 (M2 + M9, E-011). Run in ONE transaction by whoever operates the schema; the forward runner never
-- executes it. Restores the version-10 schema exactly (the catalog fingerprint equals the one recorded for version 10).
-- No data is lost: while every scope is personal, `scope_path` is derivable from `user_id`; `visibility` is dropped with it, so
-- any row that was made `project`/`shared` becomes private to its owner again. The rollback REFUSES if two users now share a
-- job idempotency key, because the global uniqueness it restores would be violated.
-- After a re-apply the new columns sit at later attribute numbers (PostgreSQL never reuses a dropped one); every other
-- catalog property is identical. Roll back later migrations first.

DO $guard$
BEGIN
  IF EXISTS (SELECT 1 FROM job GROUP BY idempotency_key HAVING count(*) > 1) THEN
    RAISE EXCEPTION 'm2_rollback_refused_idempotency_keys_collide' USING ERRCODE = 'P0001';
  END IF;
END
$guard$;

DO $m2$
DECLARE
  tbl text;
  full_tables text[] := ARRAY[
    'memory_version', 'memory_evidence', 'memory_derivation', 'memory_embedding', 'memory_conflict',
    'turn', 'session', 'job', 'audit_event', 'deletion_request',
    'association', 'abstraction_stats', 'verification'
  ];
  visibility_only text[] := ARRAY['entity', 'negative_constraint'];
BEGIN
  FOREACH tbl IN ARRAY full_tables || visibility_only LOOP
    EXECUTE pg_catalog.format('DROP POLICY %I ON %I', tbl || '_scope_read', tbl);
    EXECUTE pg_catalog.format('DROP INDEX %I', tbl || '_scope_path_idx');
  END LOOP;
  FOREACH tbl IN ARRAY full_tables LOOP
    EXECUTE pg_catalog.format('DROP TRIGGER %I ON %I', 'trg_' || tbl || '_scope_default', tbl);
    EXECUTE pg_catalog.format(
      'ALTER TABLE %I DROP CONSTRAINT %I, DROP CONSTRAINT %I, DROP CONSTRAINT %I, '
      'DROP COLUMN visibility, DROP COLUMN scope_path',
      tbl, tbl || '_scope_matches_user', tbl || '_scope_path_shape', tbl || '_visibility_check'
    );
  END LOOP;
  FOREACH tbl IN ARRAY visibility_only LOOP
    EXECUTE pg_catalog.format('ALTER TABLE %I DROP CONSTRAINT %I, DROP COLUMN visibility', tbl, tbl || '_visibility_check');
  END LOOP;
END
$m2$;

ALTER TABLE job DROP CONSTRAINT job_user_idempotency_key;
ALTER TABLE job ADD CONSTRAINT job_idempotency_key_key UNIQUE (idempotency_key);
ALTER TABLE app_user DROP CONSTRAINT app_user_id_is_scope_segment;
DROP FUNCTION fill_scope_path();
DELETE FROM _cme_schema_migrations WHERE version = 11;
