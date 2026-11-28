-- Migration 32: a child row's readability is its parent artifact's (`I7`, `V2-D113`).
--
-- Migration 11 gave every scoped table the same `*_scope_read` policy: a row is readable inside the caller's own
-- namespace, or when its own `visibility` is `project`/`shared` and an ACTIVE `scope_grant` covers its own
-- `scope_path`. For `memory_version` that is exactly the rule `I7` states. For a *child* table it is not: an
-- artifact's evidence, derivation, embedding, ledger and verification rows are not independently shareable, and a
-- second copy of "is this artifact shared" on every child row can disagree with the parent's -- which is how a
-- private artifact leaks its evidence, or a shared one renders as `missing_provenance`.
--
-- This migration removes that second copy. A child row inherits its parent's readability by a policy that delegates:
--   `EXISTS (SELECT 1 FROM <parent> AS p WHERE p.id = <child>.<fk> AND p.user_id = <child>.user_id)`
-- The parent table inside the sub-query is itself subject to its own `*_scope_read` policy (PostgreSQL evaluates the
-- policies of every relation a policy expression references), so a `private` parent hides its children from a grantee
-- and a `shared` parent exposes exactly those. The join is on `(id, user_id)`, so it cannot cross a tenant: migration 4
-- makes every child's `(version_id, user_id)` a composite foreign key into `memory_version (id, user_id)`.
-- `verification_artifact_scope_read` (migration 31) is the same shape one level up, and now chains through
-- `verification` to `memory_version`.
--
-- The writer is `api.handlers.Handlers.set_visibility`, which is an ordinary `UPDATE` of one artifact row as the
-- caller: row-level security's own-user `WITH CHECK` decides whether the caller may write it, so sharing needs no
-- privileged function and no GUC gate. `assert`/`ingest_episode`/`create_entity`/`link` take the same column at
-- insertion. The child tables still carry their migration-11 `scope_path`/`visibility` columns, and nothing updates
-- them: after a `set_visibility` the parent says `shared` while its children keep the value they were written with.
-- That disagreement is inert by construction, because no child policy reads those columns any more -- readability is
-- the parent's alone -- and that is deliberate: a synced copy is a second source of truth that can drift, and syncing it
-- would need a `SECURITY DEFINER` path. A future policy must not read a child's own `visibility`
-- (`test_migration_m2.py::test_the_read_policy_is_the_library_expression_on_every_scoped_table` pins every child to
-- `rls_parent_read`).
--
-- Ten tables keep the migration-11 policy unchanged: `memory_version`, `entity` and `association` (an artifact's own
-- visibility), `negative_constraint`, `memory_conflict`, `turn`, `session`, `job`, `audit_event`, `deletion_request`
-- (no parent artifact; they stay own-namespace only).
--
-- The SQL is the text of `scope.access.rls_parent_read`; `tests/integration/test_migration_m2.py` pins the two
-- together, exactly as it pins `rls_read_using` to the migration-11 policy.

-- 1. The five children migration 11 scoped independently: replace the policy, not the name.
DROP POLICY memory_evidence_scope_read ON memory_evidence;
CREATE POLICY memory_evidence_scope_read ON memory_evidence FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM memory_version AS p
       WHERE p.id = memory_evidence.version_id AND p.user_id = memory_evidence.user_id
    )
  );

DROP POLICY memory_derivation_scope_read ON memory_derivation;
CREATE POLICY memory_derivation_scope_read ON memory_derivation FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM memory_version AS p
       WHERE p.id = memory_derivation.derived_version_id AND p.user_id = memory_derivation.user_id
    )
  );

DROP POLICY memory_embedding_scope_read ON memory_embedding;
CREATE POLICY memory_embedding_scope_read ON memory_embedding FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM memory_version AS p
       WHERE p.id = memory_embedding.version_id AND p.user_id = memory_embedding.user_id
    )
  );

DROP POLICY abstraction_stats_scope_read ON abstraction_stats;
CREATE POLICY abstraction_stats_scope_read ON abstraction_stats FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM memory_version AS p
       WHERE p.id = abstraction_stats.version_id AND p.user_id = abstraction_stats.user_id
    )
  );

DROP POLICY verification_scope_read ON verification;
CREATE POLICY verification_scope_read ON verification FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM memory_version AS p
       WHERE p.id = verification.version_id AND p.user_id = verification.user_id
    )
  );

-- 2. The two per-artifact children added after migration 11 (`V2-D97`, `V2-D96`): a new read policy beside their
--    own-user one, so a procedure's stored shape and its outcome counters are readable wherever the procedure is.
CREATE POLICY outcome_stats_scope_read ON outcome_stats FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM memory_version AS p
       WHERE p.id = outcome_stats.version_id AND p.user_id = outcome_stats.user_id
    )
  );

CREATE POLICY procedure_shape_scope_read ON procedure_shape FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM memory_version AS p
       WHERE p.id = procedure_shape.version_id AND p.user_id = procedure_shape.user_id
    )
  );

-- 3. `ARCHITECTURE_V2.md` section 5 rule 5: scope identity is immutable. Before this migration nothing enforced it:
--    the runtime role holds UPDATE on all three artifact tables, and no guard named `scope_path`, so an artifact could
--    be re-homed in place and its provenance history silently rewritten. With `visibility` now writable by its owner,
--    the one column that decides *where* an artifact lives must be the one that cannot change. A change of scope is a
--    new artifact (`API_V2.md` section 5.2).
CREATE FUNCTION guard_scope_identity() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.scope_path IS DISTINCT FROM OLD.scope_path THEN
    RAISE EXCEPTION 'scope_identity_is_immutable' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_memory_version_scope_identity BEFORE UPDATE ON memory_version
  FOR EACH ROW EXECUTE FUNCTION guard_scope_identity();
CREATE TRIGGER trg_entity_scope_identity BEFORE UPDATE ON entity
  FOR EACH ROW EXECUTE FUNCTION guard_scope_identity();
CREATE TRIGGER trg_association_scope_identity BEFORE UPDATE ON association
  FOR EACH ROW EXECUTE FUNCTION guard_scope_identity();

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_scope_identity() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;
