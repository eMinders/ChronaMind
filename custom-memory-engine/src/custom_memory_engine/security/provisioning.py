"""Provision and verify the restricted runtime role (RL-071, Invariant 7).

Role creation is DDL, so it lives here rather than in the checksum-bound migration. The SQL text is
``rls.sql``; this module substitutes validated identifiers into it and runs each section.
"""

import re
from pathlib import Path
from typing import LiteralString, cast

from psycopg import AsyncConnection, sql
from psycopg.errors import InvalidParameterValue
from psycopg.rows import tuple_row

RLS_SQL = Path(__file__).with_name("rls.sql")
SCHEMA_PATTERN = re.compile(r"cme_[a-z][a-z0-9_]*")
ROLE_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")

# Every user-scoped table; predicate_schema is shared catalog and deliberately excluded.
USER_TABLES = (
    "app_user",
    "session",
    "turn",
    "memory_version",
    "memory_evidence",
    "memory_derivation",
    "memory_embedding",
    "memory_conflict",
    "audit_event",
    "deletion_request",
    "job",
    "scope_grant",  # migration 2 (V2-D36)
    "association",  # migration 10 (E-014)
    "entity",
    "abstraction_stats",
    "verification",
    "negative_constraint",
    "api_idempotency",  # migration 12 (E-039)
)


# Exact least-privilege grant set (#47 §3.1). Anything else on any relation in the
# engine schema — including TRUNCATE, REFERENCES or TRIGGER, which bypass or sidestep
# row-level security — is an extra grant and fails verification.
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
_APPEND = frozenset({"SELECT", "INSERT"})
_MUTABLE = frozenset({"SELECT", "INSERT", "UPDATE"})
_FULL = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
# Exact per-table grant set (V2-D54, finding S-03); mirrors the @grants section of rls.sql.
TABLE_GRANTS: dict[str, frozenset[str]] = {
    "app_user": _APPEND,
    "session": _APPEND,
    "turn": _APPEND,
    "memory_evidence": _APPEND,
    "audit_event": _APPEND,
    "deletion_request": _APPEND,
    "memory_embedding": _MUTABLE,
    "memory_conflict": _MUTABLE,
    "scope_grant": _MUTABLE,
    "memory_version": _FULL,
    "memory_derivation": _FULL,
    "job": _FULL,
    "predicate_schema": frozenset({"SELECT"}),
    # migration 10 (E-014). Associations are unlinked by the API; ledgers and constraints are updated, never deleted
    # (rows go with their version by cascade, which runs as the owner); a verification is an append-only record.
    "association": _FULL,
    "entity": _MUTABLE,
    "abstraction_stats": _MUTABLE,
    "verification": _APPEND,
    "negative_constraint": _MUTABLE,
    "api_idempotency": _APPEND,  # append-only: a recorded replay answer is never rewritten
    # the method registry is shared and append-only: anyone may read it, `register_method` appends, nobody changes it
    "method": _APPEND,
}


class RlsProvisioningError(InvalidParameterValue):
    """Static provisioning error (SQLSTATE 22023) without credentials or content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sections() -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in RLS_SQL.read_text(encoding="utf-8").splitlines():
        marker = re.match(r"^-- @([a-z-]+)\s*$", line)
        if marker:
            name = marker.group(1)
            current = name
            sections[name] = []
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _validated(schema_name: str, role_name: str) -> tuple[str, str]:
    if not isinstance(schema_name, str) or len(schema_name) > 63 or SCHEMA_PATTERN.fullmatch(schema_name) is None:
        raise RlsProvisioningError("isolated_engine_schema_identifier_required")
    if not isinstance(role_name, str) or ROLE_PATTERN.fullmatch(role_name) is None:
        raise RlsProvisioningError("valid_runtime_role_required")
    return schema_name, role_name


def _statements(section: str, schema_name: str, role_name: str) -> list[sql.Composed]:
    """Render a section into individual statements with quoted identifiers substituted."""
    rendered: list[sql.Composed] = []
    for statement in _sections()[section].split(";"):
        if not statement.strip():
            continue
        pieces: list[sql.Composable] = []
        for part in re.split(r"(\{\{role\}\}|\{\{schema\}\})", statement):
            if part == "{{role}}":
                pieces.append(sql.Identifier(role_name))
            elif part == "{{schema}}":
                pieces.append(sql.Identifier(schema_name))
            else:
                pieces.append(sql.SQL(cast(LiteralString, part)))
        rendered.append(sql.Composed(pieces))
    return rendered


async def verify_forced_rls(connection: AsyncConnection, schema_name: str) -> None:
    """Assert ENABLE and FORCE ROW LEVEL SECURITY on every user-scoped table, or raise."""
    _validated(schema_name, "placeholder")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            "SELECT count(*) FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n "
            "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r' AND c.relname = ANY(%s)",
            (schema_name, list(USER_TABLES)),
        )
        row = await cursor.fetchone()
        present = int(row[0]) if row else 0
        await cursor.execute(
            cast(LiteralString, _sections()["verify-rls"]), {"schema": schema_name, "tables": list(USER_TABLES)}
        )
        offenders = [r[0] for r in await cursor.fetchall()]
    if present != len(USER_TABLES):
        raise RlsProvisioningError("user_tables_missing")
    if offenders:
        raise RlsProvisioningError("row_level_security_not_forced")


async def verify_runtime_role(connection: AsyncConnection, role_name: str) -> None:
    """Assert the role exists and holds none of the privileges that bypass or escalate past RLS."""
    _validated("cme_placeholder", role_name)
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(cast(LiteralString, _sections()["verify-role"]), {"role": role_name})
        row = await cursor.fetchone()
    if row is None:
        raise RlsProvisioningError("runtime_role_missing")
    if any(bool(flag) for flag in row):
        raise RlsProvisioningError("runtime_role_has_dangerous_attribute")


def expected_grants(relation: str) -> frozenset[str]:
    """Return the exact privilege set the runtime role must hold on ``relation``."""
    return TABLE_GRANTS.get(relation, frozenset())


async def privilege_violations(connection: AsyncConnection, schema_name: str, role_name: str) -> list[str]:
    """Return every deviation from the least-privilege contract, as static codes.

    Checked through the catalog (``has_*_privilege``), so grants via ``PUBLIC`` or role
    membership count exactly as a direct grant would. An empty list is the pass condition.
    """
    schema_name, role_name = _validated(schema_name, role_name)
    violations: list[str] = []
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            "SELECT c.relname, p.privilege, pg_catalog.has_table_privilege(%(role)s, c.oid, p.privilege) "
            "FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
            "CROSS JOIN unnest(%(privileges)s::text[]) AS p(privilege) "
            "WHERE n.nspname = %(schema)s AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
            "ORDER BY c.relname, p.privilege",
            {"role": role_name, "schema": schema_name, "privileges": list(TABLE_PRIVILEGES)},
        )
        for relation, privilege, held in await cursor.fetchall():
            wanted = privilege in expected_grants(relation)
            if held and not wanted:
                violations.append(f"extra_{privilege.lower()}_on_{relation}")
            elif wanted and not held:
                violations.append(f"missing_{privilege.lower()}_on_{relation}")
        await cursor.execute(
            "SELECT pg_catalog.has_schema_privilege(%(role)s, %(schema)s, 'CREATE'), "
            "pg_catalog.has_schema_privilege(%(role)s, %(schema)s, 'USAGE'), "
            "pg_catalog.has_schema_privilege(%(role)s, 'public', 'CREATE'), "
            "pg_catalog.has_database_privilege(%(role)s, pg_catalog.current_database(), 'CREATE'), "
            "EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members AS m "
            "        JOIN pg_catalog.pg_roles AS r ON r.oid = m.member WHERE r.rolname = %(role)s)",
            {"role": role_name, "schema": schema_name},
        )
        row = await cursor.fetchone()
    if row is None:
        return [*violations, "privilege_catalog_unreadable"]
    schema_create, schema_usage, public_create, database_create, has_memberships = row
    if schema_create:
        violations.append("extra_create_on_engine_schema")
    if not schema_usage:
        violations.append("missing_usage_on_engine_schema")
    if public_create:
        violations.append("extra_create_on_public_schema")
    if database_create:
        violations.append("extra_create_on_database")
    if has_memberships:
        violations.append("runtime_role_inherits_other_roles")
    return violations


async def verify_privileges(connection: AsyncConnection, schema_name: str, role_name: str) -> None:
    """Raise unless the role holds exactly the least-privilege grant set."""
    if await privilege_violations(connection, schema_name, role_name):
        raise RlsProvisioningError("runtime_role_privileges_not_least")


async def provision_runtime_role(
    connection: AsyncConnection,
    schema_name: str,
    role_name: str,
    *,
    login_role: str | None = None,
) -> None:
    """Create the restricted role, grant least privilege, and verify the result.

    ``login_role`` (optional) is granted membership so a login role can ``SET ROLE`` to it; the runtime
    role itself never has ``LOGIN``. Raises ``RlsProvisioningError`` if forced RLS or the role's
    attributes are not as required. Runs on the caller's connection; the caller owns commit semantics.
    """
    schema_name, role_name = _validated(schema_name, role_name)
    if login_role is not None and ROLE_PATTERN.fullmatch(login_role) is None:
        raise RlsProvisioningError("valid_login_role_required")
    for section in ("create-role", "grants"):
        for statement in _statements(section, schema_name, role_name):
            await connection.execute(statement)
    if login_role is not None:
        await connection.execute(
            sql.SQL("GRANT {} TO {}").format(sql.Identifier(role_name), sql.Identifier(login_role))
        )
    await verify_forced_rls(connection, schema_name)
    await verify_runtime_role(connection, role_name)
    await verify_privileges(connection, schema_name, role_name)


__all__ = [
    "TABLE_GRANTS",
    "USER_TABLES",
    "RlsProvisioningError",
    "expected_grants",
    "privilege_violations",
    "provision_runtime_role",
    "verify_forced_rls",
    "verify_privileges",
    "verify_runtime_role",
]
