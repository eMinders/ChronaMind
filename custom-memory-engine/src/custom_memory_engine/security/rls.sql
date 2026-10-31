-- Restricted runtime role and RLS verification (RL-071, Invariant 7).
--
-- Applied by custom_memory_engine.security.provisioning, which substitutes the two
-- placeholders with validated, quoted identifiers. Role creation is DDL and is kept out of
-- the migration path on purpose (the runner is checksum-bound to a single version).
--
--   {{role}}    the restricted runtime role (NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT)
--   {{schema}}  the isolated cme_* schema holding the engine tables (migrations 1 and 2)
--
-- Sections are split on the marker lines below.

-- @create-role
CREATE ROLE {{role}} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT

-- @grants
GRANT USAGE ON SCHEMA {{schema}} TO {{role}};
GRANT SELECT, INSERT, UPDATE, DELETE ON
  {{schema}}.app_user, {{schema}}.session, {{schema}}.turn, {{schema}}.memory_version,
  {{schema}}.memory_evidence, {{schema}}.memory_derivation, {{schema}}.memory_embedding,
  {{schema}}.memory_conflict, {{schema}}.audit_event, {{schema}}.deletion_request, {{schema}}.job,
  {{schema}}.scope_grant
  TO {{role}};
-- predicate_schema is shared catalog, not user data: read-only.
GRANT SELECT ON {{schema}}.predicate_schema TO {{role}};

-- @verify-rls
-- Returns one row per user-scoped table that lacks ENABLE or FORCE ROW LEVEL SECURITY.
-- An empty result is the pass condition (asserted by provisioning.verify_forced_rls).
SELECT c.relname
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s
  AND c.relkind = 'r'
  AND c.relname = ANY(%(tables)s)
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
ORDER BY c.relname

-- @verify-role
-- Returns the role's dangerous attributes; every column must be false.
SELECT r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolcanlogin
FROM pg_catalog.pg_roles AS r
WHERE r.rolname = %(role)s
