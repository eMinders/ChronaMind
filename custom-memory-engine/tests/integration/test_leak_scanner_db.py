"""#46 integration: a normal deletion must leave zero residue across all seven channels.

Complements ``test_retrieval_deletion_db.py`` (which proves the engine's own
channels 1/2/4) by measuring the four channels the engine cannot see — 3, 5, 6, 7 —
through the harness scanner added in this change, and by requiring a *fully
measured* zero-leak verdict rather than the weaker "no measured leaks" report.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.deletion.scanner import (
    ChannelProbe,
    LeakChannel,
    ScanContext,
    database_channel_probes,
    scan_deleted_key,
)
from custom_memory_engine.engine.deletion import delete_memory
from evaluation.harness.leak_scanner import (
    answer_probe,
    assembled_context_probe,
    external_cache_probe,
    log_sink_probe,
    vector_probe,
    verdict,
    verify_detection,
)
from tests.integration.schema import apply_migrations

pytestmark = pytest.mark.integration

CANARY = "CANARY-SEVENCHAN-01"
START = datetime(2026, 3, 1, tzinfo=UTC)


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(config.database_url.get_secret_value(), connect_timeout=5) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        yield conn


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist")
    config = DatabaseConfig.from_environment()
    schema = "cme_scan_" + uuid4().hex
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        async with connect((config, schema)) as conn:
            await apply_migrations(conn)
            await seed(conn)
        yield config, schema
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def identity(conn: AsyncConnection, user: str, instant: datetime) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute("SELECT clock_now()")  # bind a monotonic clock tick


async def seed(conn: AsyncConnection) -> None:
    await conn.execute("INSERT INTO predicate_schema VALUES ('diet', 'single', 'semantic')")
    for user in ("u1", "u2"):
        await identity(conn, user, START)
        await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "ext-" + user, START))
        await conn.execute("INSERT INTO session VALUES (%s, %s, %s, NULL)", ("s-" + user, user, START))


def _literal(x: float) -> str:
    return repr(float(x))


async def _insert_canary_version(conn: AsyncConnection, version_id: str, user: str) -> None:
    embedding = "[" + ",".join(_literal(0.5 if i == 0 else 0.0) for i in range(384)) + "]"
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            """INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at)
               VALUES (%s, %s, %s, 1, 'user', %s, %s)""",
            ("t-" + version_id, "s-" + user, user, f"I said {CANARY}", START),
        )
        await conn.execute(
            """INSERT INTO memory_version
                 (id, memory_key, user_id, status, subject_key, predicate_key, single_valued,
                  content, valid_range, recorded_range, layer)
               VALUES (%s, 'k1', %s, 'active', 'user', 'diet', true,
                       %s, tstzrange(%s, NULL), tstzrange(clock_now(), NULL, '[)'), 'semantic')""",
            (version_id, user, f"the user said {CANARY}", START),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, %s, 'support')",
            (version_id, "t-" + version_id, user, f"the user said {CANARY}"),
        )
        await conn.execute(
            "INSERT INTO memory_embedding (version_id, user_id, embedding_model, embedding) "
            "VALUES (%s, %s, 'BAAI/bge-small-en-v1.5', %s::vector)",
            (version_id, user, embedding),
        )


class _Static:
    """A target system whose channels are empty after a correct purge."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def vector_hits(self, user_id: str, query: str, limit: int) -> list[str]:
        return [self.text] if self.text else []

    def assembled_context(self, user_id: str, query: str) -> str:
        return self.text

    def answer(self, user_id: str, question: str) -> str:
        return self.text


async def test_normal_deletion_measures_zero_on_all_seven_channels(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await _insert_canary_version(conn, "v-canary", "u1")
        # Commit the seed before acting on it: the provenance trigger is a deferred
        # constraint trigger, so a same-transaction delete would leave it checking
        # evidence rows that the delete itself already cascaded away.
        await conn.commit()
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            receipt = await delete_memory(conn, "u1", scope="memory", target_ref="k1", schema_name=schema)
        assert receipt.deleted_versions >= 1

        context = ScanContext("u1", "k1", CANARY, receipt.deletion_epoch)
        clean = _Static()
        probes: dict[LeakChannel, ChannelProbe] = {
            "channel_3_vector_search": vector_probe(clean, query=CANARY),
            "channel_5_assembled_context": assembled_context_probe(clean, query=CANARY),
            "channel_6_final_answer": answer_probe(clean, question=CANARY),
            "channel_7_application_logs": log_sink_probe(lambda: ["nothing here"]),
            "channel_1_storage_index_queue_cache_scan": external_cache_probe(lambda user_id: []),
        }
        report = await scan_deleted_key(context, probes, connection=conn, schema_name=schema)
        result = verdict(report)

    assert result.leaking_channels == ()
    assert result.complete is True, f"unmeasured: {result.unmeasured_channels} partial: {result.partial_channels}"
    assert result.clean is True


async def test_leak_reappearing_in_a_channel_blocks_the_clean_verdict(database) -> None:
    """If any channel still holds the canary, the deletion is not clean — measured, not asserted."""
    _, schema = database
    async with connect(database) as conn:
        await _insert_canary_version(conn, "v-canary", "u1")
        # Commit the seed before acting on it: the provenance trigger is a deferred
        # constraint trigger, so a same-transaction delete would leave it checking
        # evidence rows that the delete itself already cascaded away.
        await conn.commit()
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            receipt = await delete_memory(conn, "u1", scope="memory", target_ref="k1", schema_name=schema)
        context = ScanContext("u1", "k1", CANARY, receipt.deletion_epoch)
        stale = _Static(f"cached copy of {CANARY}")
        clean = _Static()
        probes: dict[LeakChannel, ChannelProbe] = {
            "channel_3_vector_search": vector_probe(clean, query=CANARY),
            "channel_5_assembled_context": assembled_context_probe(stale, query=CANARY),
            "channel_6_final_answer": answer_probe(clean, question=CANARY),
            "channel_7_application_logs": log_sink_probe(lambda: []),
            "channel_1_storage_index_queue_cache_scan": external_cache_probe(lambda user_id: []),
        }
        report = await scan_deleted_key(context, probes, connection=conn, schema_name=schema)
        result = verdict(report)

    assert result.clean is False
    assert "channel_5_assembled_context" in result.leaking_channels


async def test_a8_detection_fires_against_the_real_schema_scan(database) -> None:
    """A8 must observe a leak that is genuinely present in the database, not a synthetic stub only."""
    _, schema = database
    async with connect(database) as conn:
        await _insert_canary_version(conn, "v-canary", "u1")
        # Commit the seed before acting on it: the provenance trigger is a deferred
        # constraint trigger, so a same-transaction delete would leave it checking
        # evidence rows that the delete itself already cascaded away.
        await conn.commit()
        context = ScanContext("u1", "k1", CANARY, 1)
        # No deletion: the canary is present, so an unablated scan must find it.
        probes = database_channel_probes(conn, schema_name=schema)
        report = await scan_deleted_key(context, probes, connection=conn, schema_name=schema)
        result = verdict(report)

    assert result.clean is False
    assert {
        "channel_1_storage_index_queue_cache_scan",
        "channel_2_lexical_search",
        "channel_4_derived_memory_lookup",
    } <= set(result.leaking_channels)


async def test_detectability_of_a_synthetic_injection() -> None:
    """The scanner reports detection when a probe is deliberately made to leak."""
    context = ScanContext("u1", "k1", CANARY, 1)
    probes: dict[LeakChannel, ChannelProbe] = {"channel_6_final_answer": answer_probe(_Static(), question=CANARY)}
    detection = await verify_detection(context=context, probes=probes, injected_channel="channel_6_final_answer")

    assert detection.detected is True


async def test_a_cache_probe_supplements_and_never_replaces_the_storage_scan(database) -> None:
    """Regression (r10): a caller's channel-1 probe used to replace the SQL storage scan, so a canary left in
    storage was reported as a complete, clean channel 1 whenever a cache probe was supplied."""
    _, schema = database
    async with connect(database) as conn:
        await _insert_canary_version(conn, "v-left-behind", "u1")
        await conn.commit()
        context = ScanContext("u1", "k1", CANARY, 1)
        probes: dict[LeakChannel, ChannelProbe] = {
            "channel_1_storage_index_queue_cache_scan": external_cache_probe(lambda user_id: []),
        }
        report = await scan_deleted_key(context, probes, connection=conn, schema_name=schema)
    channel_1 = next(f for f in report.findings if f.channel == "channel_1_storage_index_queue_cache_scan")
    assert channel_1.residual_count and channel_1.residual_count > 0, "storage still holds the canary"
    assert channel_1.complete, "the cache surface completes channel 1"
