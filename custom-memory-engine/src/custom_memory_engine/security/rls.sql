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
-- Least privilege, derived from every statement the engine issues (V2-D54, finding S-03).
-- Append-only records (audit, deletion receipts, turns, sessions, users) get no UPDATE or DELETE,
-- so not even a compromised runtime can rewrite audit history or erase a deletion receipt.
-- UPDATE where held is needed by SELECT ... FOR UPDATE or ON CONFLICT DO UPDATE as well as updates.
-- Cascaded deletes run as the table owner, so a child table needs no DELETE for ON DELETE CASCADE.
-- Mirrored exactly by provisioning.TABLE_GRANTS, and verification fails on any difference.
GRANT USAGE ON SCHEMA {{schema}} TO {{role}};
GRANT SELECT, INSERT ON
  {{schema}}.app_user, {{schema}}.session, {{schema}}.turn, {{schema}}.memory_evidence,
  {{schema}}.audit_event, {{schema}}.deletion_request, {{schema}}.api_idempotency
  TO {{role}};
GRANT SELECT, INSERT ON {{schema}}.scope, {{schema}}.entity_link TO {{role}};
GRANT SELECT, INSERT, UPDATE ON
  {{schema}}.memory_embedding, {{schema}}.memory_conflict, {{schema}}.scope_grant,
  {{schema}}.entity, {{schema}}.abstraction_stats, {{schema}}.negative_constraint
  TO {{role}};
GRANT SELECT, INSERT, UPDATE, DELETE ON
  {{schema}}.memory_version, {{schema}}.memory_derivation, {{schema}}.job, {{schema}}.association
  TO {{role}};
-- predicate_schema is shared catalog, not user data: read-only.
GRANT SELECT ON {{schema}}.predicate_schema TO {{role}};
-- verification is an append-only record, and method is the shared append-only registry (E-014).
GRANT SELECT, INSERT ON {{schema}}.verification, {{schema}}.method TO {{role}};

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
