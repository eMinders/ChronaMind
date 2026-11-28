"""Migration 17: ``tool``/``file``/``event`` evidence is stored under its declared type (V2-D66; E-021 #136).

Migration 16 admitted the three types in ``memory_evidence_source_type_check`` but left the shape rule and the source
trigger knowing only ``external``, so no such row could exist and the API recorded every one as ``external``. These
tests run against the REAL migrated schema; the API test runs as a ``NOSUPERUSER NOBYPASSRLS`` role.
"""

from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, RaiseException

from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.engine.sources import fetch_sources
from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_api_wiring_structure import caller, world
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture)
from tests.integration.test_migration_m4 import EXTERNAL_HASH, insert_version, seed_turns

pytestmark = pytest.mark.integration
M17 = 17
DECLARED = ("tool", "file", "event")


async def insert_reference(
    conn: AsyncConnection, version_id: str, source_type: str, *, turn_id: str | None = None
) -> None:
    """A reference row as a writer sends it: no legacy ``evidence_span`` (the trigger mirrors it from ``span``)."""
    await conn.execute(
        "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_role, source_type, source_ref, span, "
        "content_hash) VALUES (%s, %s, 'u1', 'support', %s, %s, 'line 3', %s)",
        (version_id, turn_id, source_type, f"{source_type}://x", EXTERNAL_HASH),
    )


@pytest.mark.parametrize("source_type", DECLARED)
async def test_a_declared_reference_row_is_stored_under_its_own_type(database, source_type: str) -> None:  # noqa: F811
    async with connect(database) as conn:
        async with conn.transaction():
            await insert_version(conn, "v1")
            await insert_reference(conn, "v1", source_type)
        assert await rows(conn, "SELECT turn_id, source_type, source_ref, evidence_span FROM memory_evidence") == [
            (None, source_type, f"{source_type}://x", "line 3")
        ]


@pytest.mark.parametrize("source_type", DECLARED)
async def test_a_declared_reference_row_cannot_carry_a_turn(database, source_type: str) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_turns(conn)
        with pytest.raises(RaiseException, match="external_source_has_no_turn"):
            async with conn.transaction():
                await insert_version(conn, "bad")
                await insert_reference(conn, "bad", source_type, turn_id="t-plain")
        # the shape rule refuses it too, independently of the trigger
        await conn.execute("ALTER TABLE memory_evidence DISABLE TRIGGER trg_memory_evidence_source_dual_write")
        try:
            with pytest.raises(CheckViolation, match="turn_source_check"):
                async with conn.transaction():
                    await insert_version(conn, "bad")
                    await conn.execute(
                        "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role, "
                        "source_type, source_ref, span, content_hash) "
                        "VALUES ('bad', 't-plain', 'u1', 's', 'r', %s, 'x://y', 's', %s)",
                        (source_type, EXTERNAL_HASH),
                    )
        finally:
            await conn.execute("ALTER TABLE memory_evidence ENABLE TRIGGER trg_memory_evidence_source_dual_write")


@pytest.mark.parametrize("source_type", DECLARED)
async def test_the_api_records_the_type_the_caller_declared(database, source_type: str) -> None:  # noqa: F811
    async with world(database) as (conn, engine, schema):
        assert isinstance(engine.resolver, DbTargets)
        register_ingest_handlers(engine, engine.resolver.runtime)
        source = {"type": source_type, "ref": f"{source_type}:run", "span": "1 passed", "content_hash": EXTERNAL_HASH}
        receipt = await engine.call(
            caller("alice"),
            "ingest_episode",
            {"scope": "/u/alice/", "source": source, "content": "it ran", "event_time": None, "idempotency_key": "k"},
        )
        assert isinstance(receipt, Receipt)
        await conn.execute("SELECT set_config('app.user_id', 'alice', false)")
        stored = await fetch_sources(conn, "alice", receipt.id, schema_name=schema)
        assert [(s.source_type, s.source_ref, s.span) for s in stored] == [
            (source_type, f"{source_type}:run", "1 passed")
        ]
        assert stored[0].in_deletion_boundary is False  # a reference, like `external`: no engine row to delete


async def test_the_source_trigger_stays_pinned(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        config = await rows(
            conn,
            "SELECT p.proconfig FROM pg_catalog.pg_proc p "
            "WHERE p.pronamespace = current_schema()::regnamespace AND p.proname = 'sync_memory_evidence_source'",
        )
        schema = (await rows(conn, "SELECT current_schema()"))[0][0]
        assert config == [([f"search_path={schema}, pg_catalog, pg_temp"],)]


async def test_rollback_restores_version_16_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M17 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M17 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical: constraint, function and its pin
    assert (await runner.status()).version == M17 - 1
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn, conn.transaction():
        await insert_version(conn, "v1")
        await insert_reference(conn, "v1", "tool")


async def test_rollback_refuses_while_a_declared_reference_row_exists(database) -> None:  # noqa: F811
    """Version 16 cannot store the row; rewriting it as ``external`` would erase the type it was recorded with."""
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        async with conn.transaction():
            await insert_version(conn, "v1")
            await insert_reference(conn, "v1", "event")
        with pytest.raises(RaiseException, match="m17_rollback_refused_declared_source_rows_exist"):
            await conn.execute(cast(LiteralString, rollback_sql(from_version=M17, to_version=M17 - 1)), prepare=False)
        assert await rows(conn, "SELECT source_type FROM memory_evidence") == [("event",)]
    assert (await runner.status()).version == migrate.VERSION
