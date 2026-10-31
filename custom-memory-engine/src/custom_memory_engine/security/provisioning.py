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
)


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


__all__ = [
    "USER_TABLES",
    "RlsProvisioningError",
    "provision_runtime_role",
    "verify_forced_rls",
    "verify_runtime_role",
]
