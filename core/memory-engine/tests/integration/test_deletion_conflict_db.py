"""#46 regression: deleting a belief that was superseded through the gate must succeed.

Found by the Suite D runner. The gate records a ``memory_conflict`` row whose
``existing_version_id`` names the superseded version; that column had no
``ON DELETE`` action, so ``delete_memory`` on the memory key raised
``ForeignKeyViolation`` and the deletion did not happen at all (R6).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import LiteralString, cast

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql

from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.ingest import TurnInput, apply_candidate, persist_turn
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput
from tests.integration.schema import engine_database

pytestmark = pytest.mark.integration

T0 = datetime(2025, 5, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def conn() -> AsyncIterator[tuple[AsyncConnection, str]]:
    async with (
        engine_database() as (config, schema),
        await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as connection,
    ):
        await connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await connection.execute(
            "INSERT INTO predicate_schema VALUES ('badge', 'single', 'semantic'), ('desk', 'single', 'semantic')"
        )
        await connection.execute("SELECT set_config('app.user_id', 'u1', false)")
        yield connection, schema


async def _state(connection: AsyncConnection, schema: str, index: int, key: str, text: str) -> None:
    moment = T0 + timedelta(minutes=index + 1)
    async with connection.transaction():
        await persist_turn(
            connection,
            TurnInput(f"t{index}", "u1", "s1", index + 1, "user", text, moment),
            schema_name=schema,
            create_user=True,
        )
    async with connection.transaction():
        await apply_candidate(
            connection,
            CandidateVersionInput(
                version_id=f"v{index}",
                memory_key=key,
                user_id="u1",
                subject_key="user",
                predicate_key=key,
                content=text,
                valid_from=moment,
                evidence=(EvidenceInput(turn_id=f"t{index}", evidence_span=text, evidence_role="user"),),
            ),
            evidence_turns=(("user", text),),
            schema_name=schema,
            replay_time=moment,
        )


async def _count(connection: AsyncConnection, query: str) -> int:
    cursor = await connection.execute(cast(LiteralString, query))
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def test_deleting_a_gate_superseded_belief_removes_every_version(conn) -> None:
    connection, schema = conn
    await _state(connection, schema, 0, "badge", "My badge serial is AAA111")
    await _state(connection, schema, 1, "badge", "My badge serial is now BBB222")
    assert await _count(connection, "SELECT count(*) FROM memory_conflict WHERE existing_version_id IS NOT NULL") == 1

    async with connection.transaction():
        receipt = await delete_memory(
            connection,
            "u1",
            scope="memory",
            target_ref="badge",
            schema_name=schema,
            replay_time=T0 + timedelta(hours=1),
        )

    assert receipt.deleted_versions == 3  # original, its closed re-issue, and the update
    assert await _count(connection, "SELECT count(*) FROM memory_version WHERE memory_key = 'badge'") == 0
    assert await _count(connection, "SELECT count(*) FROM memory_conflict WHERE existing_version_id IS NOT NULL") == 0


async def test_survivor_decision_record_is_kept_without_a_dangling_reference(conn) -> None:
    """A conflict row whose candidate survives keeps its decision but loses the deleted id."""
    connection, schema = conn
    await _state(connection, schema, 0, "badge", "My badge serial is AAA111")
    await _state(connection, schema, 1, "desk", "My desk number is D42")
    # Point the surviving 'desk' decision at the version about to be deleted (cross-key reference).
    await connection.execute("UPDATE memory_conflict SET existing_version_id = 'v0' WHERE candidate_version_id = 'v1'")

    async with connection.transaction():
        await delete_memory(
            connection,
            "u1",
            scope="memory",
            target_ref="badge",
            schema_name=schema,
            replay_time=T0 + timedelta(hours=1),
        )

    assert await _count(connection, "SELECT count(*) FROM memory_conflict WHERE candidate_version_id = 'v1'") == 1
    assert await _count(connection, "SELECT count(*) FROM memory_conflict WHERE existing_version_id = 'v0'") == 0
    assert await _count(connection, "SELECT count(*) FROM memory_version WHERE memory_key = 'desk'") == 1
