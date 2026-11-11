"""Migration 18: ``entity_link``, the scope-local entity substrate (E-024 #139, ``V2-D71``).

Migration 18 adds one table and no scope table: a scope *is* its canonical path (``V2-D65``), and migration 15 already
admits a scoped row anywhere inside its owner's namespace. These tests run against the REAL migrated schema and check
the guarantees the table itself holds: both endpoints are one principal's own entities, the pair has one stored
orientation, a link goes with either endpoint, and the rollback restores version 17 exactly or refuses while a link
exists.
"""

from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import ForeignKeyViolation, RaiseException, UniqueViolation

from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture; seeds u1 and u2)

pytestmark = pytest.mark.integration
M18 = 18


async def entity(conn: AsyncConnection, entity_id: str, user: str, scope: str) -> None:
    await conn.execute(
        "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type) "
        "VALUES (%s, %s, %s, 'Ada', 'person')",
        (entity_id, user, scope),
    )


async def link(conn: AsyncConnection, link_id: str, a: str, b: str, relation: str = "same_person") -> None:
    """Insert a link the way ``link_entity`` does: the pair in its canonical orientation."""
    first, second = sorted((a, b))
    await conn.execute(
        "INSERT INTO entity_link (id, user_id, entity_a, entity_b, relation) VALUES (%s, 'u1', %s, %s, %s)",
        (link_id, first, second, relation),
    )


async def test_the_pair_is_stored_in_one_orientation_per_relation(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/")
        await entity(conn, "e2", "u1", "/u/u1/p/repo/")  # a sub-scope with no row of its own (V2-D65)
        await link(conn, "l1", "e2", "e1")  # asked in the other order
        assert await rows(conn, "SELECT entity_a, entity_b FROM entity_link") == [("e1", "e2")]
        await link(conn, "l3", "e1", "e2", "colleague")  # a different relation is a different link
        with pytest.raises(UniqueViolation):  # the same pair and relation, in either order, is one row
            await link(conn, "l2", "e1", "e2")


async def test_the_reverse_orientation_is_refused_by_the_table(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/")
        await entity(conn, "e2", "u1", "/u/u1/")
        with pytest.raises(Exception, match="entity_link_ordered_entities"):
            await conn.execute(
                "INSERT INTO entity_link (id, user_id, entity_a, entity_b, relation) "
                "VALUES ('l1', 'u1', 'e2', 'e1', 'same_person')"
            )


async def test_both_endpoints_must_be_the_same_principals_entities(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        async with conn.transaction():
            await entity(conn, "e1", "u1", "/u/u1/")
        async with connect(database, user="u2") as other, other.transaction():
            await entity(other, "e2", "u2", "/u/u2/")
        for target in ("e2", "not-an-entity"):  # another principal's entity and a missing one: the same refusal
            with pytest.raises(ForeignKeyViolation):
                async with conn.transaction():
                    await link(conn, "l1", "e1", target)


async def test_a_link_is_removed_with_either_endpoint(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/")
        await entity(conn, "e2", "u1", "/u/u1/")
        await link(conn, "l1", "e1", "e2")
        await conn.execute("DELETE FROM entity WHERE id = 'e2'")
        assert await rows(conn, "SELECT count(*) FROM entity_link") == [(0,)]


async def test_rollback_restores_version_17_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M18 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M18 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical: the table is the whole migration
    assert (await runner.status()).version == M18 - 1
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/")
        await entity(conn, "e2", "u1", "/u/u1/")
        await link(conn, "l1", "e1", "e2")


async def test_rollback_refuses_while_a_link_exists(database) -> None:  # noqa: F811
    """Version 17 has nowhere to keep the link; dropping it would destroy cross-scope identity silently."""
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        async with conn.transaction():
            await entity(conn, "e1", "u1", "/u/u1/")
            await entity(conn, "e2", "u1", "/u/u1/p/repo/")
            await link(conn, "l1", "e1", "e2")
        with pytest.raises(RaiseException, match="entity_link_rows_present_rollback_refused"):
            await conn.execute(cast(LiteralString, rollback_sql(from_version=M18, to_version=M18 - 1)), prepare=False)
        assert await rows(conn, "SELECT count(*) FROM entity_link") == [(1,)]
    assert (await runner.status()).version == migrate.VERSION
