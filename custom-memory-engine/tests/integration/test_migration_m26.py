"""Migration 26: the entity vector slot (``V2-D101``), its pairing rule, and a rollback that refuses to lose vectors.

* ``embedding_1024`` and ``embedding_model`` are **paired**: a vector without the model that produced it cannot be
  compared safely (the vector stage filters by model), and a model name without a vector is a lie about the row;
* the rollback restores the version-25 catalog exactly, and **refuses** while any entity carries a vector unless the
  operator names that loss in the same transaction (``V2-D107``), like rollbacks 8, 11 and 25.
"""

from datetime import UTC, datetime
from typing import LiteralString, cast

import pytest
from psycopg import sql
from psycopg.errors import CheckViolation, RaiseException

from custom_memory_engine.engine.hybrid import vector_literal
from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
M26 = 26
T0 = datetime(2026, 3, 10, tzinfo=UTC)
VECTOR = vector_literal([1.0] + [0.0] * 1023)


async def seed(conn, *, with_vector: bool) -> None:
    # `u1` and `app.user_id` are set by the fixture's `connect`
    await conn.execute(
        "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases, embedding_1024,"
        " embedding_model) VALUES ('e1', 'u1', '/u/u1/', 'layla hassan', 'person', '{}', %s::halfvec(1024), %s)",
        (VECTOR if with_vector else None, "test/axes" if with_vector else None),
    )


async def test_a_vector_and_its_model_are_written_together_or_not_at_all(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed(conn, with_vector=False)
        for vector, model in ((VECTOR, None), (None, "test/axes")):
            with pytest.raises(CheckViolation):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE entity SET embedding_1024 = %s::halfvec(1024), embedding_model = %s WHERE id = 'e1'",
                        (vector, model),
                    )


async def test_rollback_restores_version_25_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M26 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M26 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()"
            " AND table_name = 'entity' AND column_name IN ('embedding_1024', 'embedding_model')",
        )
    assert (await runner.status()).version == M26 - 1
    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION


async def test_rollback_refuses_while_vectors_exist_and_proceeds_only_when_the_loss_is_named(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    down = cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M26 - 1))
    async with connect(database) as conn:
        await seed(conn, with_vector=True)
        for setting in (None, "export_snapshot"):
            with pytest.raises(RaiseException, match="m26_rollback_refused_entity_vectors_exist"):
                async with conn.transaction():
                    if setting is not None:
                        await conn.execute(sql.SQL("SET LOCAL cme.rollback_discard = {}").format(sql.Literal(setting)))
                    await conn.execute(down, prepare=False)
        # nothing was half-done: the column, the vector and the history row are all still there
        assert await rows(conn, "SELECT embedding_model FROM entity WHERE id = 'e1'") == [("test/axes",)]
        assert await rows(conn, "SELECT max(version) FROM _cme_schema_migrations") == [(migrate.VERSION,)]
        async with conn.transaction():
            await conn.execute("SET LOCAL cme.rollback_discard = 'entity_embedding'")
            await conn.execute(down, prepare=False)
        # the entity itself survives a downgrade; only its vector goes
        assert await rows(conn, "SELECT canonical_name FROM entity WHERE id = 'e1'") == [("layla hassan",)]
    assert (await runner.status()).version == M26 - 1
    assert (await runner.apply()).action == "applied"


async def test_migration_27_rolls_back_to_the_recorded_version_26_catalog(database) -> None:  # noqa: F811
    """Migration 27 (`V2-D108`) adds the entity erasure function and touches no row: lossless both ways."""
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        down = cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=26))
        await conn.execute(down, prepare=False)
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = 26")
        assert await runner._catalog(conn) == recorded[0][0]
        assert not await rows(conn, "SELECT 1 FROM pg_catalog.pg_proc WHERE proname = 'delete_orphaned_entities'")
    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION
