"""B-01 integration proof: exact subtree matching on the supported PostgreSQL version.

Reproduces the reported cross-tenant leak against a live server, then proves the
replacement predicate is exact. Requires CME_TEST_DATABASE_URL (integration mark).
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.scope.paths import normalize_read_prefixes, subtree_predicate

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[AsyncConnection]:
    config = DatabaseConfig.from_environment()
    async with await AsyncConnection.connect(config.database_url.get_secret_value(), row_factory=tuple_row) as conn:
        yield conn


async def test_like_predicate_leaks_across_tenants_on_postgres_16(connection: AsyncConnection) -> None:
    """The documented defect: `_` in a selector matches a different tenant's character."""
    async with connection.cursor() as cursor:
        await cursor.execute("SELECT %s::text LIKE %s::text", ("/u/aXb/p/repo/", "/u/a_b/%"))
        row = await cursor.fetchone()
    assert row is not None and row[0] is True, "expected the historical LIKE leak to reproduce"


async def test_starts_with_predicate_is_exact_for_wildcard_identifiers(connection: AsyncConnection) -> None:
    clause, params = subtree_predicate("t.path", ["/u/a_b/"])
    assert "LIKE" not in clause.as_string(connection).upper()
    async with connection.cursor() as cursor:
        await cursor.execute("CREATE TEMP TABLE t (path text NOT NULL) ON COMMIT DROP")
        await cursor.execute("INSERT INTO t VALUES ('/u/a_b/p/repo/'), ('/u/aXb/p/repo/')")
        await cursor.execute(sql.SQL("SELECT path FROM t WHERE {} ORDER BY path").format(clause), params)
        visible = [row[0] for row in await cursor.fetchall()]
    assert visible == ["/u/a_b/p/repo/"], "the other tenant's row must not be selected"


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("/u/alice/", ["/u/alice/", "/u/alice/projects/apollo/", "/u/alice/projects/apollox/"]),
        ("/u/alice/projects/", ["/u/alice/projects/apollo/", "/u/alice/projects/apollox/"]),
        ("/u/alice/projects/apollo/", ["/u/alice/projects/apollo/"]),
        ("/u/alic/", ["/u/alic/"]),  # a path is within itself (inclusive)
        ("/u/zoe/", []),
        ("/u/%/", []),
        ("/u/a_/", []),
    ],
)
async def test_subtree_selection_matches_segment_semantics(
    connection: AsyncConnection, selector: str, expected: list[str]
) -> None:
    clause, params = subtree_predicate("t.path", [selector])
    async with connection.cursor() as cursor:
        await cursor.execute("CREATE TEMP TABLE t (path text NOT NULL) ON COMMIT DROP")
        await cursor.execute(
            "INSERT INTO t VALUES ('/u/alice/'), ('/u/alice/projects/apollo/'),"
            " ('/u/alice/projects/apollox/'), ('/u/alic/'), ('/u/bob/'), ('/u/aXb/')"
        )
        await cursor.execute(sql.SQL("SELECT path FROM t WHERE {} ORDER BY path").format(clause), params)
        visible = [row[0] for row in await cursor.fetchall()]
    assert visible == expected


async def test_union_of_selectors_is_exact_and_deduplicated(connection: AsyncConnection) -> None:
    selectors = normalize_read_prefixes(["/u/alice/", "/u/bob/", "/u/alice/"])
    assert selectors == ("/u/alice/", "/u/bob/")
    clause, params = subtree_predicate("t.path", selectors)
    async with connection.cursor() as cursor:
        await cursor.execute("CREATE TEMP TABLE t (path text NOT NULL) ON COMMIT DROP")
        await cursor.execute("INSERT INTO t VALUES ('/u/alice/'), ('/u/bob/'), ('/u/carol/'), ('/u/a_b/')")
        await cursor.execute(sql.SQL("SELECT path FROM t WHERE {} ORDER BY path").format(clause), params)
        visible = [row[0] for row in await cursor.fetchall()]
    assert visible == ["/u/alice/", "/u/bob/"]


async def test_text_pattern_ops_index_can_serve_the_predicate(connection: AsyncConnection) -> None:
    """`starts_with` must be indexable; a predicate that cannot use an index is a DoS surface."""
    clause, params = subtree_predicate("t.path", ["/u/alice/"])
    async with connection.cursor() as cursor:
        await cursor.execute("CREATE TEMP TABLE t (path text NOT NULL) ON COMMIT DROP")
        await cursor.execute("CREATE INDEX ON t (path text_pattern_ops)")
        await cursor.execute("INSERT INTO t SELECT '/u/alice/' || g || '/' FROM generate_series(1, 200) AS g")
        await cursor.execute("ANALYZE t")
        await cursor.execute(sql.SQL("SELECT count(*) FROM t WHERE {}").format(clause), params)
        row = await cursor.fetchone()
    assert row is not None and row[0] == 200
