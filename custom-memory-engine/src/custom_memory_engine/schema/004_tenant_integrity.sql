-- Migration 4: tenant integrity in the database itself — V2-D54 (pre-W1 audit findings S-01, S-02).
--
-- S-01  Foreign keys bypass row-level security. PostgreSQL runs referential-integrity checks
--       and actions as the table owner without row security, so every single-column key below
--       let one tenant reference another tenant's row: cite another tenant's turn as evidence,
--       derive from another tenant's version, and thereby (a) probe whether an id exists and
--       (b) block that tenant's own deletion with a foreign-key violation it cannot see or fix.
--       Each intra-tenant key becomes composite on (id, user_id), so a reference to another
--       tenant's row fails exactly as a reference to a missing row does.
--       `scope_grant.principal_id` stays single-column on purpose: a grant names another user.
--
-- S-02  Trigger functions resolved tables through the caller's search_path, and pg_temp is
--       searched first for relations. Any role with TEMPORARY (PUBLIC has it by default) could
--       shadow `memory_evidence` with a temporary table and commit an active version without
--       evidence. Every engine function is pinned to `<this schema>, pg_catalog, pg_temp`;
--       naming pg_temp last means temporary objects can never shadow engine tables.
--
-- Also: grants are revoked by setting `revoked_at`, never deleted (migration 2's own contract),
-- so the owner DELETE policy on `scope_grant` is dropped (the runtime role loses DELETE too).
--
-- Additive and fail-closed: if existing data already holds a cross-tenant reference, adding the
-- composite key fails and the whole migration rolls back.

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'clock_now()', 'guard_update()', 'enforce_provenance()', 'enforce_archival_invariant()',
    'guard_derivation_coverage()', 'guard_scope_grant_update()', 'enforce_association_cap()'
  ] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;

ALTER TABLE session ADD CONSTRAINT session_id_user_id_key UNIQUE (id, user_id);
ALTER TABLE turn ADD CONSTRAINT turn_id_user_id_key UNIQUE (id, user_id);
ALTER TABLE memory_version ADD CONSTRAINT memory_version_id_user_id_key UNIQUE (id, user_id);

ALTER TABLE turn
  DROP CONSTRAINT turn_session_id_fkey,
  ADD CONSTRAINT turn_session_same_tenant
    FOREIGN KEY (session_id, user_id) REFERENCES session (id, user_id);

ALTER TABLE memory_evidence
  DROP CONSTRAINT memory_evidence_version_id_fkey,
  DROP CONSTRAINT memory_evidence_turn_id_fkey,
  ADD CONSTRAINT memory_evidence_version_same_tenant
    FOREIGN KEY (version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE,
  ADD CONSTRAINT memory_evidence_turn_same_tenant
    FOREIGN KEY (turn_id, user_id) REFERENCES turn (id, user_id);

ALTER TABLE memory_derivation
  DROP CONSTRAINT memory_derivation_derived_version_id_fkey,
  DROP CONSTRAINT memory_derivation_source_version_id_fkey,
  ADD CONSTRAINT memory_derivation_derived_same_tenant
    FOREIGN KEY (derived_version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE,
  ADD CONSTRAINT memory_derivation_source_same_tenant
    FOREIGN KEY (source_version_id, user_id) REFERENCES memory_version (id, user_id);

ALTER TABLE memory_embedding
  DROP CONSTRAINT memory_embedding_version_id_fkey,
  ADD CONSTRAINT memory_embedding_version_same_tenant
    FOREIGN KEY (version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE;

ALTER TABLE memory_conflict
  DROP CONSTRAINT memory_conflict_candidate_version_id_fkey,
  DROP CONSTRAINT memory_conflict_existing_version_id_fkey,
  ADD CONSTRAINT memory_conflict_candidate_same_tenant
    FOREIGN KEY (candidate_version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE,
  ADD CONSTRAINT memory_conflict_existing_same_tenant
    FOREIGN KEY (existing_version_id, user_id) REFERENCES memory_version (id, user_id);

ALTER TABLE job
  DROP CONSTRAINT job_turn_id_fkey,
  ADD CONSTRAINT job_turn_same_tenant
    FOREIGN KEY (turn_id, user_id) REFERENCES turn (id, user_id);

DROP POLICY scope_grant_delete ON scope_grant;
