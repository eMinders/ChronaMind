"""B-08 / V2-D13: the association degree cap is enforced by PostgreSQL, including under concurrency.

The trigger is attached to a table shaped like M6's ``ASSOCIATION`` (E-014 creates the
real one) inside a schema migrated to the current version through the runner.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import ProgramLimitExceeded, RaiseException

from custom_memory_engine.model.association import (
    ASSOCIATION_TYPES,
    DEFAULT_ASSOCIATION_CAP,
    AssociationPolicyError,
    attach_cap_trigger,
    degree_allows,
    validate_cap,
)
from tests.integration.schema import engine_database

pytestmark = pytest.mark.integration
CAP = 4


@pytest_asyncio.fixture
async def env() -> AsyncIterator[tuple[str, str]]:
    async with engine_database() as (config, schema):
        url = config.database_url.get_secret_value()
        async with await AsyncConnection.connect(url, autocommit=True) as conn:
            await conn.execute(
                sql.SQL(
                    "CREATE TABLE {} (id text PRIMARY KEY, from_id text NOT NULL, to_id text NOT NULL, type text)"
                ).format(sql.Identifier(schema, "association_probe"))
            )
            await conn.execute(attach_cap_trigger(schema, "association_probe", CAP))
        yield url, schema


async def insert(url: str, schema: str, edge: str, a: str, b: str) -> None:
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(
            sql.SQL("INSERT INTO {} VALUES (%s, %s, %s, 'related_to')").format(
                sql.Identifier(schema, "association_probe")
            ),
            (edge, a, b),
        )


async def test_degree_cap_counts_both_directions_and_rejects_the_next_edge(env) -> None:
    url, schema = env
    await insert(url, schema, "e1", "hub", "x1")
    await insert(url, schema, "e2", "x2", "hub")  # incoming edges count too
    await insert(url, schema, "e3", "hub", "x3")
    await insert(url, schema, "e4", "x4", "hub")
    with pytest.raises(ProgramLimitExceeded, match="association_cap_exceeded"):
        await insert(url, schema, "e5", "hub", "x5")
    await insert(url, schema, "e6", "x5", "x6")  # unrelated endpoints are unaffected


async def test_update_cannot_bypass_the_cap(env) -> None:
    url, schema = env
    for index in range(CAP):
        await insert(url, schema, f"h{index}", "hub", f"y{index}")
    await insert(url, schema, "other", "p", "q")
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        with pytest.raises(ProgramLimitExceeded):
            await conn.execute(
                sql.SQL("UPDATE {} SET to_id = 'hub' WHERE id = 'other'").format(
                    sql.Identifier(schema, "association_probe")
                )
            )
        # Re-pointing an edge that already touches the hub does not count itself twice.
        await conn.execute(
            sql.SQL("UPDATE {} SET to_id = 'z9' WHERE id = 'h0'").format(sql.Identifier(schema, "association_probe"))
        )


async def test_self_loops_are_rejected(env) -> None:
    url, schema = env
    with pytest.raises(RaiseException, match="association_self_loop_rejected"):
        await insert(url, schema, "loop", "a", "a")


async def test_concurrent_writers_cannot_overshoot_the_cap(env) -> None:
    """Twenty concurrent inserts on one hub: exactly CAP may commit."""
    url, schema = env
    results = await asyncio.gather(
        *(insert(url, schema, f"c{index}", "hub", f"n{index}") for index in range(20)), return_exceptions=True
    )
    committed = sum(result is None for result in results)
    assert committed == CAP
    assert all(isinstance(result, ProgramLimitExceeded) for result in results if result is not None)
    async with await AsyncConnection.connect(url) as conn:
        cursor = await conn.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE 'hub' IN (from_id, to_id)").format(
                sql.Identifier(schema, "association_probe")
            )
        )
        assert (await cursor.fetchone() or (0,))[0] == CAP


async def test_trigger_rejects_a_malformed_cap_argument(env) -> None:
    url, schema = env
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(
            sql.SQL("CREATE TABLE {} (id text, from_id text, to_id text)").format(sql.Identifier(schema, "bad"))
        )
        await conn.execute(
            sql.SQL("CREATE TRIGGER t BEFORE INSERT ON {} FOR EACH ROW EXECUTE FUNCTION {}('0')").format(
                sql.Identifier(schema, "bad"), sql.Identifier(schema, "enforce_association_cap")
            )
        )
        with pytest.raises(RaiseException, match="association_cap_out_of_range"):
            await conn.execute(sql.SQL("INSERT INTO {} VALUES ('e', 'a', 'b')").format(sql.Identifier(schema, "bad")))


def test_policy_defaults_and_validation() -> None:
    assert DEFAULT_ASSOCIATION_CAP == 32
    assert len(ASSOCIATION_TYPES) == 10
    assert degree_allows(31) and not degree_allows(32)
    for bad in (0, 1025, True, 3.0, "32"):
        with pytest.raises(AssociationPolicyError):
            validate_cap(bad)
    with pytest.raises(AssociationPolicyError):
        attach_cap_trigger("cme_x", "association", 0)
