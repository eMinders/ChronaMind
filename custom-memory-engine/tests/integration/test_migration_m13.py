"""Migration 13: the generic predicates the public API writes under (E-039)."""

from typing import LiteralString, cast

import pytest

from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import (
    connect,
    database,  # noqa: F401  (fixture)
    rows,
)

pytestmark = pytest.mark.integration
M13 = 13


async def test_both_generic_predicates_are_multi_valued(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        assert await rows(
            conn,
            "SELECT predicate_key, cardinality, default_layer FROM predicate_schema "
            "WHERE predicate_key IN ('episode', 'statement') ORDER BY 1",
        ) == [("episode", "multi", "episodic"), ("statement", "multi", "semantic")]


@pytest.mark.parametrize(
    "existing", [("statement", "single", "semantic"), ("episode", "multi", "semantic")], ids=["single", "layer"]
)
async def test_an_existing_predicate_that_means_something_else_refuses_the_migration(
    database,  # noqa: F811
    existing: tuple[str, str, str],
) -> None:
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M13 - 1)), prepare=False
        )
        await conn.execute("INSERT INTO predicate_schema VALUES (%s, %s, %s)", existing)
        before = await runner._catalog(conn)
    with pytest.raises(migrate.MigrationError):
        await runner.apply()
    async with connect(database) as conn:
        assert await runner._catalog(conn) == before
        assert await rows(conn, "SELECT max(version) FROM _cme_schema_migrations") == [(M13 - 1,)]
        assert await rows(
            conn, "SELECT cardinality, default_layer FROM predicate_schema WHERE predicate_key = %s", (existing[0],)
        ) == [existing[1:]]


async def test_an_identical_existing_predicate_is_kept(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M13 - 1)), prepare=False
        )
        await conn.execute("INSERT INTO predicate_schema VALUES ('statement', 'multi', 'semantic')")
    assert (await runner.apply()).status.version == migrate.VERSION
