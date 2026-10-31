"""MA-011 isolation suite on PostgreSQL 16: both layers, each alone (``V2-D45``).

* **Matching predicate.** Every case runs against the SQL twin of each candidate matcher on a scratch
  table. The two historical ``LIKE`` designs fail exactly the cases they were reported for; the
  decided ``starts_with`` passes all, and so does the library ``read_predicate``.
* **RLS layer alone.** A scratch table carries the policy from ``scope.access.rls_read_using``. Under
  the restricted runtime role, with **no application filter at all**, every caller sees exactly the
  rows ``acl_readable`` allows, for every grant set, after revocation, and with an unusable identity.

It needs no M2 migration; ``E-011`` re-runs the cases against the real schema.
"""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.scope.access import acl_readable, read_predicate, resolve_read_set, rls_read_using
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from tests.scope_isolation_cases import CASES, EXPECTED_FAILURES, MATCHERS, ROWS, USERS, Case

pytestmark = pytest.mark.integration

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "src/custom_memory_engine/schema"
T0 = datetime(2026, 3, 10, tzinfo=UTC)


def _url() -> str:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist")
    return DatabaseConfig.from_environment().database_url.get_secret_value()


@pytest_asyncio.fixture
async def rows_table() -> AsyncIterator[AsyncConnection]:
    async with await AsyncConnection.connect(_url(), connect_timeout=5) as conn:
        await conn.execute("CREATE TEMP TABLE iso_row (scope_path text NOT NULL, visibility text NOT NULL)")
        async with conn.cursor() as cursor:
            await cursor.executemany("INSERT INTO iso_row VALUES (%s, %s)", ROWS)
        yield conn
        await conn.rollback()


async def _select(conn: AsyncConnection, where: sql.Composable, params: list[object]) -> frozenset[tuple[str, str]]:
    async with conn.cursor() as cursor:
        await cursor.execute(sql.SQL("SELECT scope_path, visibility FROM iso_row WHERE {}").format(where), params)
        return frozenset((str(a), str(b)) for a, b in await cursor.fetchall())


def _matcher_where(case: Case, template: str) -> tuple[sql.Composable, list[object]]:
    """The selector semantics of ``read_predicate`` with the subtree test replaced by ``template``."""
    path, visibility = sql.Identifier("scope_path"), sql.Identifier("visibility")
    subtree = sql.SQL(cast(LiteralString, template)).format(p=path)
    clauses: list[sql.Composable] = []
    params: list[object] = []
    for selector in case.selectors:
        wanted = sorted(selector.visibilities)
        exact = sql.SQL("({} = %s AND {} = ANY(%s::text[]))").format(path, visibility)
        if selector.mode == "exact":
            clauses.append(exact)
            params += [selector.path.text, wanted]
            continue
        if "private" in wanted:
            clauses.append(exact)
            params += [selector.path.text, ["private"]]
        shared = [v for v in wanted if v != "private"]
        if shared:
            clauses.append(sql.SQL("({} AND {} = ANY(%s::text[]))").format(subtree, visibility))
            params += [selector.path.text, shared]
    return sql.SQL(" OR ").join(clauses), params


@pytest.mark.parametrize("matcher", sorted(MATCHERS))
async def test_each_sql_matcher_fails_exactly_its_reported_cases(rows_table: AsyncConnection, matcher: str) -> None:
    failing = set()
    for case in CASES:
        where, params = _matcher_where(case, MATCHERS[matcher][1])
        if await _select(rows_table, where, params) != case.expected:
            failing.add(case.number)
    assert failing == EXPECTED_FAILURES[matcher]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"case{c.number}")
async def test_library_read_predicate_returns_exactly_the_expected_rows(
    rows_table: AsyncConnection, case: Case
) -> None:
    read_set = resolve_read_set(case.caller, case.selectors, case.grants)
    where, params = read_predicate(read_set, "scope_path", "visibility")
    assert await _select(rows_table, where, params) == case.expected


# --- RLS layer alone ---------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def rls_env() -> AsyncIterator[tuple[AsyncConnection, str]]:
    schema, role = "cme_iso_" + uuid4().hex[:12], "cme_rt_" + uuid4().hex[:12]
    async with await AsyncConnection.connect(_url(), autocommit=True, connect_timeout=5) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        for name in ("001_full_schema.sql", "002_scope_grants.sql"):
            await conn.execute(cast(LiteralString, (SCHEMA_DIR / name).read_text(encoding="utf-8")), prepare=False)
        await provision_runtime_role(conn, schema, role)
        for user in USERS:
            await conn.execute("SELECT set_config('app.user_id', %s, false)", (user,))
            await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "ext-" + user, T0))
        await conn.execute("SELECT set_config('app.user_id', '', false)")
        await conn.execute("CREATE TABLE iso_row (scope_path text NOT NULL, visibility text NOT NULL)")
        await conn.execute("ALTER TABLE iso_row ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE iso_row FORCE ROW LEVEL SECURITY")
        await conn.execute(
            sql.SQL("CREATE POLICY iso_row_read ON iso_row FOR SELECT USING {}").format(
                rls_read_using("iso_row", grant_table=(schema, "scope_grant"))
            )
        )
        await conn.execute(sql.SQL("GRANT SELECT ON iso_row TO {}").format(sql.Identifier(role)))
        async with conn.cursor() as cursor:
            await cursor.executemany("INSERT INTO iso_row VALUES (%s, %s)", ROWS)
        try:
            yield conn, role
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def _rls_rows(conn: AsyncConnection, role: str, caller: str) -> frozenset[tuple[str, str]]:
    async with tenant_scope(conn, caller, role=role):
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT scope_path, visibility FROM iso_row")  # no application filter
            return frozenset((str(a), str(b)) for a, b in await cursor.fetchall())


async def _grant(conn: AsyncConnection, role: str, owner: str, principal: str, path: str) -> str:
    grant_id = "g-" + uuid4().hex[:12]
    async with tenant_scope(conn, owner, role=role):
        await conn.execute(
            "INSERT INTO scope_grant (id, owner_user_id, principal_id, scope_path, granted_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (grant_id, owner, principal, path, T0),
        )
    return grant_id


def _expected(caller: str, grants: tuple[str, ...]) -> frozenset[tuple[str, str]]:
    return frozenset(r for r in ROWS if acl_readable(caller, grants, *r))


async def test_rls_alone_returns_exactly_the_acl_for_every_caller(rls_env) -> None:
    conn, role = rls_env
    for user in USERS:
        assert await _rls_rows(conn, role, user) == _expected(user, ()), user
    assert ("/u/aXb/", "private") not in await _rls_rows(conn, role, "a_b")
    assert not any(p.startswith("/u/42/") for p, _ in await _rls_rows(conn, role, "4"))


async def test_rls_alone_honours_grants_never_private_and_revocation(rls_env) -> None:
    conn, role = rls_env
    grant_id = await _grant(conn, role, "42", "4", "/u/42/")
    await _grant(conn, role, "40", "4", "/u/40/p/")
    seen = await _rls_rows(conn, role, "4")
    assert seen == _expected("4", ("/u/42/", "/u/40/p/"))
    assert ("/u/42/", "shared") in seen and ("/u/42/", "private") not in seen
    assert ("/u/40/p/a/", "shared") in seen
    assert await _rls_rows(conn, role, "4x") == _expected("4x", ()), "a grant to 4 is not a grant to 4x"
    async with tenant_scope(conn, "42", role=role):
        await conn.execute("UPDATE scope_grant SET revoked_at = %s WHERE id = %s", (T0, grant_id))
    assert await _rls_rows(conn, role, "4") == _expected("4", ("/u/40/p/",))


@pytest.mark.parametrize("identity", [None, "", "4/p", "a_b/p"])
async def test_rls_alone_reads_nothing_for_an_unusable_identity(rls_env, identity: str | None) -> None:
    conn, role = rls_env
    async with conn.transaction():
        await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        if identity is not None:
            await conn.execute("SELECT set_config('app.user_id', %s, true)", (identity,))
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT count(*) FROM iso_row")
            row = await cursor.fetchone()
    assert row is not None and row[0] == 0


async def test_the_unqualified_policy_column_leaks_which_is_why_it_is_qualified(rls_env) -> None:
    """Mutation check: with the row column unqualified, it binds to ``scope_grant.scope_path``."""
    conn, role = rls_env
    await _grant(conn, role, "42", "4", "/u/42/")
    qualified = rls_read_using("iso_row").as_string(conn)
    unqualified = qualified.replace('"iso_row".', "")
    assert unqualified != qualified
    await conn.execute("DROP POLICY iso_row_read ON iso_row")
    await conn.execute(cast(LiteralString, f"CREATE POLICY iso_row_read ON iso_row FOR SELECT USING {unqualified}"))
    leaked = await _rls_rows(conn, role, "4") - _expected("4", ("/u/42/",))
    assert ("/u/4x/", "project") in leaked and ("/u/aXb/p/r/", "project") in leaked
