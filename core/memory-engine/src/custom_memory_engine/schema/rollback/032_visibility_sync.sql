-- Rollback of migration 32: a child row's readability follows its parent artifact.
-- Run in ONE transaction; restores the version-31 schema. Roll back later migrations first.
--
-- The five policies this migration REPLACED are recreated from migration 11's own text, so the catalog fingerprint of
-- version 31 is restored exactly (policy expressions are part of it). The two policies it added are dropped.

DROP TRIGGER trg_association_scope_identity ON association;
DROP TRIGGER trg_entity_scope_identity ON entity;
DROP TRIGGER trg_memory_version_scope_identity ON memory_version;
DROP FUNCTION guard_scope_identity();

DROP POLICY outcome_stats_scope_read ON outcome_stats;
DROP POLICY procedure_shape_scope_read ON procedure_shape;

DROP POLICY memory_evidence_scope_read ON memory_evidence;
DROP POLICY memory_derivation_scope_read ON memory_derivation;
DROP POLICY memory_embedding_scope_read ON memory_embedding;
DROP POLICY abstraction_stats_scope_read ON abstraction_stats;
DROP POLICY verification_scope_read ON verification;

DO $m2$
DECLARE
  tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY[
    'memory_evidence', 'memory_derivation', 'memory_embedding', 'abstraction_stats', 'verification'
  ] LOOP
    EXECUTE pg_catalog.format(
      'CREATE POLICY %I ON %I FOR SELECT USING ('
      '(pg_catalog.strpos(COALESCE(NULLIF(pg_catalog.current_setting(''app.user_id'', true), ''''), ''/''), ''/'') = 0 AND ('
      'pg_catalog.starts_with(%2$I.scope_path, ''/u/'' || NULLIF(pg_catalog.current_setting(''app.user_id'', true), '''') || ''/'') '
      'OR (%2$I.visibility IN (''project'', ''shared'') AND EXISTS ('
      'SELECT 1 FROM scope_grant AS g WHERE g.principal_id = NULLIF(pg_catalog.current_setting(''app.user_id'', true), '''') '
      'AND g.revoked_at IS NULL AND pg_catalog.starts_with(%2$I.scope_path, g.scope_path))))))',
      tbl || '_scope_read', tbl
    );
  END LOOP;
END
$m2$;

DELETE FROM _cme_schema_migrations WHERE version = 32;
