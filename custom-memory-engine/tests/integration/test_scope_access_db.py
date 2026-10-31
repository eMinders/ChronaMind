"""B-02 / V2-D36 on PostgreSQL, under the restricted runtime role.

* the grant table enforces owner-namespace, no-self-grant and append-only revocation;
* RLS hides a grant from everyone but its owner and grantee;
* live resolution honours revocation;
* the SQL ``read_predicate`` agrees with ``row_visible`` on every row for every read set.
"""

import itertools
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, InsufficientPrivilege, RaiseException

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.scope.access import Selector, read_predicate, resolve, row_visible
from custom_memory_engine.security import provision_runtime_role, tenant_scope

pytestmark = pytest.mark.integration

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "src/custom_memory_engine/schema"
T0 = datetime(2026, 3, 10, tzinfo=UTC)
ROWS = [
    (path, visibility)
    for path in ("/u/alice/", "/u/alice/projects/a/", "/u/alice/projects/b/", "/u/alice/projects/b/x/", "/u/bob/")
    for visibility in ("private", "project", "shared")
]


@pytest_asyncio.fixture
async def env() -> AsyncIterator[tuple[AsyncConnection, str, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist")
    url = DatabaseConfig.from_environment().database_url.get_secret_value()
    schema, role = "cme_acl_" + uuid4().hex[:12], "cme_rt_" + uuid4().hex[:12]
    async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=5) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        for name in ("001_full_schema.sql", "002_scope_grants.sql"):
            await conn.execute(cast(LiteralString, (SCHEMA_DIR / name).read_text(encoding="utf-8")), prepare=False)
        await provision_runtime_role(conn, schema, role)
        for user in ("alice", "bob", "carol"):
            await conn.execute("SELECT set_config('app.user_id', %s, false)", (user,))
            await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "ext-" + user, T0))
        await conn.execute("SELECT set_config('app.user_id', '', false)")
        try:
            yield conn, schema, role
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def grant(conn: AsyncConnection, role: str, owner: str, principal: str, path: str, grant_id: str) -> None:
    async with tenant_scope(conn, owner, role=role):
        await conn.execute(
            "INSERT INTO scope_grant (id, owner_user_id, principal_id, scope_path, granted_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (grant_id, owner, principal, path, T0),
        )


async def test_owner_grants_and_grantee_resolves_then_revocation_removes_access(env) -> None:
    conn, schema, role = env
    await grant(conn, role, "alice", "bob", "/u/alice/projects/b/", "g1")
    selectors = [Selector.of("/u/alice/projects/b/")]
    async with tenant_scope(conn, "bob", role=role):
        read = await resolve(conn, "bob", selectors, schema_name=schema)
    assert [e.path for e in read.entries] == ["/u/alice/projects/b/"]

    async with tenant_scope(conn, "alice", role=role):
        await conn.execute("UPDATE scope_grant SET revoked_at = %s WHERE id = 'g1'", (T0,))
    async with tenant_scope(conn, "bob", role=role):
        read = await resolve(conn, "bob", selectors, schema_name=schema)
    assert read.empty and read.denied[0].reason == "no_active_grant"


async def test_grant_is_invisible_to_a_third_party(env) -> None:
    conn, _, role = env
    await grant(conn, role, "alice", "bob", "/u/alice/", "g1")
    async with tenant_scope(conn, "carol", role=role):
        cursor = await conn.execute("SELECT count(*) FROM scope_grant")
        assert (await cursor.fetchone() or (1,))[0] == 0


async def test_a_tenant_cannot_grant_inside_another_tenants_namespace(env) -> None:
    conn, _, role = env
    with pytest.raises(CheckViolation):  # owner-namespace CHECK
        await grant(conn, role, "bob", "carol", "/u/alice/", "g-steal")
    with pytest.raises(InsufficientPrivilege):  # RLS WITH CHECK: cannot claim to be the owner
        async with tenant_scope(conn, "bob", role=role):
            await conn.execute(
                "INSERT INTO scope_grant (id, owner_user_id, principal_id, scope_path, granted_at) "
                "VALUES ('g-forge', 'alice', 'bob', '/u/alice/', %s)",
                (T0,),
            )


async def test_wildcard_shaped_grant_path_is_rejected_by_the_schema(env) -> None:
    conn, _, role = env
    with pytest.raises(CheckViolation):
        await grant(conn, role, "alice", "bob", "/u/alice/a_%/", "g-wild")


async def test_self_grants_and_rewrites_are_rejected_and_revocation_is_final(env) -> None:
    conn, _, role = env
    with pytest.raises(CheckViolation):
        await grant(conn, role, "alice", "alice", "/u/alice/", "g-self")
    await grant(conn, role, "alice", "bob", "/u/alice/", "g1")
    with pytest.raises(RaiseException, match="scope_grant_only_revocation_may_change"):
        async with tenant_scope(conn, "alice", role=role):
            await conn.execute("UPDATE scope_grant SET principal_id = 'carol', revoked_at = %s", (T0,))
    async with tenant_scope(conn, "alice", role=role):
        await conn.execute("UPDATE scope_grant SET revoked_at = %s", (T0,))
    with pytest.raises(RaiseException, match="scope_grant_already_revoked"):
        async with tenant_scope(conn, "alice", role=role):
            await conn.execute("UPDATE scope_grant SET revoked_at = NULL")


async def test_grantee_cannot_revoke_or_extend_a_grant(env) -> None:
    conn, _, role = env
    await grant(conn, role, "alice", "bob", "/u/alice/projects/b/", "g1")
    async with tenant_scope(conn, "bob", role=role):
        cursor = await conn.execute("UPDATE scope_grant SET revoked_at = %s", (T0,))
        assert cursor.rowcount == 0
        cursor = await conn.execute("DELETE FROM scope_grant")
        assert cursor.rowcount == 0


async def test_sql_predicate_agrees_with_reference_semantics_on_every_row(env) -> None:
    conn, schema, role = env
    await grant(conn, role, "alice", "bob", "/u/alice/projects/b/", "g1")
    candidates = [
        ("alice", [Selector.of("/u/alice/")]),
        ("alice", [Selector.of("/u/alice/projects/", ["project"])]),
        ("bob", [Selector.of("/u/alice/projects/b/")]),
        ("bob", [Selector.of("/u/alice/projects/b/x/", ["shared"])]),
        ("bob", [Selector.of("/u/alice/")]),
        ("bob", [Selector.of("/u/bob/"), Selector.of("/u/alice/projects/b/")]),
        ("carol", [Selector.of("/u/alice/projects/b/")]),
    ]
    await conn.execute("CREATE TEMP TABLE probe_rows (scope_path text, visibility text)")
    async with conn.cursor() as cursor:
        await cursor.executemany("INSERT INTO probe_rows VALUES (%s, %s)", ROWS)
    checked = 0
    for caller, selectors in candidates:
        async with tenant_scope(conn, caller, role=role):
            read = await resolve(conn, caller, selectors, schema_name=schema)
        clause, params = read_predicate(read, "scope_path", "visibility")
        cursor = await conn.execute(
            sql.SQL("SELECT scope_path, visibility FROM probe_rows WHERE {} ORDER BY 1, 2").format(clause), params
        )
        from_sql = [tuple(row) for row in await cursor.fetchall()]
        reference = sorted(row for row in ROWS if row_visible(read, *row))
        assert from_sql == reference, (caller, [s.path.text for s in selectors])
        checked += len(ROWS)
    assert checked == len(ROWS) * len(candidates)
    # Semantics, not only agreement: bob's grant never exposes alice's private rows, nor a sibling project.
    async with tenant_scope(conn, "bob", role=role):
        bob = await resolve(conn, "bob", [Selector.of("/u/alice/projects/b/")], schema_name=schema)
    exposed = {row for row in ROWS if row_visible(bob, *row)}
    assert exposed == {
        ("/u/alice/projects/b/", "project"),
        ("/u/alice/projects/b/", "shared"),
        ("/u/alice/projects/b/x/", "project"),
        ("/u/alice/projects/b/x/", "shared"),
    }
    assert not any(visibility == "private" for _, visibility in itertools.chain(exposed))
