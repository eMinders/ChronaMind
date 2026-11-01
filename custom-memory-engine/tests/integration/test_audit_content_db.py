"""RL-072 on PostgreSQL: the audit backstop CHECK, and Channel 1 now scans audit rows (Invariant 8)."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation
from psycopg.types.json import Jsonb

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.deletion.scanner import ProbeMeasurement, ScanContext, database_channel_probes
from tests.integration.schema import apply_migrations

pytestmark = pytest.mark.integration

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CANARY = "zephyrine-canary-41"


@pytest_asyncio.fixture
async def env() -> AsyncIterator[tuple[AsyncConnection, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable test database")
    url = DatabaseConfig.from_environment().database_url.get_secret_value()
    schema = "cme_audit_" + uuid4().hex[:16]
    async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=5) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await apply_migrations(conn)
        await conn.execute("INSERT INTO app_user VALUES ('u1', 'ext-u1', %s)", (T0,))
        try:
            yield conn, schema
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _audit(conn: AsyncConnection, details: dict[str, object], memory_ref: str = "ref") -> None:
    await conn.execute(
        "INSERT INTO audit_event VALUES (%s, 'u1', %s, 'probe', %s, %s)",
        ("a-" + uuid4().hex, memory_ref, T0, Jsonb(details)),
    )


@pytest.mark.parametrize("key", ["content", "evidence_span", "embedding"])
async def test_backstop_check_rejects_each_forbidden_top_level_key(env: tuple[AsyncConnection, str], key: str) -> None:
    conn, _ = env
    with pytest.raises(CheckViolation, match="audit_details_content_free"):
        await _audit(conn, {key: "x"})


async def test_backstop_check_is_only_a_blacklist_which_is_why_the_guard_exists(
    env: tuple[AsyncConnection, str],
) -> None:
    conn, _ = env
    await _audit(conn, {"memory_key": "user:health_condition", "note": CANARY})  # the database accepts it


async def _storage_residual(conn: AsyncConnection, schema: str) -> int:
    probe = database_channel_probes(conn, schema_name=schema)["channel_1_storage_index_queue_cache_scan"]
    measured = await probe(ScanContext(user_id="u1", memory_key="never-used-key", canary=CANARY, deletion_epoch=1))
    assert isinstance(measured, ProbeMeasurement)
    return measured.residual_count


async def test_channel_1_counts_a_canary_left_in_audit_details_or_memory_ref(env: tuple[AsyncConnection, str]) -> None:
    conn, schema = env
    assert await _storage_residual(conn, schema) == 0
    await _audit(conn, {"version_id": "v1"})  # content-free: not counted
    assert await _storage_residual(conn, schema) == 0
    await _audit(conn, {"note": f"user said {CANARY}"})  # bypasses the guard, as a buggy writer would
    await _audit(conn, {"version_id": "v2"}, memory_ref=CANARY)
    assert await _storage_residual(conn, schema) == 2
