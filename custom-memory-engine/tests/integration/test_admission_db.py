"""Recording a soundness decision (E-015): content-free, tenant-checked, in the caller's transaction."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.engine.admission import (
    LedgerCounts,
    MethodRef,
    SoundnessCandidate,
    assess_soundness,
    record_soundness_assessment,
)

pytestmark = pytest.mark.integration

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src/custom_memory_engine/schema/001_full_schema.sql"
CANDIDATE = SoundnessCandidate(("m1", "m2"), MethodRef("cooccurrence", "3", "abc123"), LedgerCounts(5, 0, 45), 3, True)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable test database")
    config = DatabaseConfig.from_environment()
    schema = "cme_adm_" + uuid4().hex[:20]
    async with await AsyncConnection.connect(config.database_url.get_secret_value(), autocommit=True) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute(cast(LiteralString, SCHEMA_SQL.read_text(encoding="utf-8")), prepare=False)
        await conn.execute("INSERT INTO app_user (id, external_ref, created_at) VALUES ('u1', 'e1', now())")
    try:
        yield config, schema
    finally:
        async with await AsyncConnection.connect(config.database_url.get_secret_value(), autocommit=True) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(config.database_url.get_secret_value(), autocommit=True) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        yield conn


async def test_a_soundness_decision_is_recorded_as_a_content_free_audit_event(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    assessment = assess_soundness(CANDIDATE, tau_confidence=0.9, tau_coverage=0.6, method_registered=lambda _m: True)
    async with connect(database) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            event_id = await record_soundness_assessment(
                conn,
                user_id="u1",
                candidate=CANDIDATE,
                assessment=assessment,
                tau_confidence=0.9,
                tau_coverage=0.6,
                schema_name=schema,
            )
        cursor = await conn.execute(
            "SELECT event_type, details, memory_ref FROM audit_event WHERE id = %s", (event_id,)
        )
        row = await cursor.fetchone()
    assert row is not None
    event_type, details, memory_ref = row
    assert event_type == "soundness_assessed"
    assert details["opportunity"] == 50 and details["undetermined"] == 45
    assert details["creation_allowed"] is True and details["admissible_by_bound"] is False
    assert float(details["confidence_lower"]) == pytest.approx(0.0333, abs=5e-5)
    assert not {"content", "evidence_span", "embedding", "statement", "text"} & set(details)
    assert "m1" not in str(details) and "m1" not in memory_ref, "source ids are counted, never recorded"


async def test_a_mismatched_tenant_context_is_refused_and_nothing_is_written(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    assessment = assess_soundness(CANDIDATE, tau_confidence=0.9, tau_coverage=0.6, method_registered=lambda _m: True)
    async with connect(database) as conn:
        with pytest.raises(InsufficientPrivilege, match="admission_user_context_mismatch"):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'someone-else', true)")
                await record_soundness_assessment(
                    conn,
                    user_id="u1",
                    candidate=CANDIDATE,
                    assessment=assessment,
                    tau_confidence=0.9,
                    tau_coverage=0.6,
                    schema_name=schema,
                )
        cursor = await conn.execute("SELECT count(*) FROM audit_event")
        assert (await cursor.fetchone() or (-1,))[0] == 0
