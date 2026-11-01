"""RL-092: replay clock monotonicity (R3, R8) and worker-queue idempotency (Invariant 9) as one gate.

Clause map. Each clause of the RL-092 contract (``PLAN/TASK_GRAPH.md``) names the tests that prove it; the
meta-test below fails if any of them disappears. The end-to-end gate in this file is the clause the ledger
recorded as missing ("no gate proves that a replayed job yields zero duplicate memory versions end to end").
"""

import ast
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql

from custom_memory_engine.clock import ClockTick
from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput, commit_initial_version
from custom_memory_engine.queue import Job, NewJob, enqueue, process_one
from tests.integration.schema import apply_migrations

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CLAUSES: dict[str, tuple[str, ...]] = {
    "out-of-order session sorting": ("tests/test_protocols.py::test_stable_session_sort_and_tie_increment",),
    "+1 us tie increment": (
        "tests/test_clock.py::test_repeated_and_out_of_order_inputs_produce_exact_microsecond_ticks",
        "tests/integration/test_clock_queue_db.py::"
        "test_replay_watermark_survives_connections_ties_and_out_of_order_without_memory_rows",
        "tests/integration/test_replay_gate_db.py::test_end_to_end_replay_is_monotone_and_never_duplicates",
    ),
    "t > rho rejection": ("tests/test_predicates.py::test_deserialization_cannot_bypass_mode_or_future_guards",),
    "beta > clock rejection": (
        "tests/integration/test_predicates_db.py::test_future_guards_use_injected_database_clock_including_empty_results",
    ),
    "SKIP LOCKED concurrency": (
        "tests/integration/test_clock_queue_db.py::test_skip_locked_claims_the_next_due_job_without_waiting_for_locked_row",
        "tests/integration/test_clock_queue_db.py::test_two_workers_serialize_same_user_handlers_and_commit_each_job_once",
    ),
    "zero duplicates on job retry": (
        "tests/integration/test_clock_queue_db.py::"
        "test_transient_failures_retry_fresh_whole_transactions_with_one_committed_memory",
        "tests/integration/test_queue_regressions_db.py::"
        "test_deferred_commit_conflicts_retry_the_whole_transaction_without_duplicate_effects",
        "tests/integration/test_replay_gate_db.py::test_end_to_end_replay_is_monotone_and_never_duplicates",
    ),
}

T0 = datetime(2026, 3, 10, 9, tzinfo=UTC)
# Turn times as a replayed session delivers them: out of order, with a tie and a backwards step.
TURN_TIMES = {"t1": T0, "t2": T0 + timedelta(hours=2), "t3": T0 + timedelta(hours=2), "t4": T0 + timedelta(hours=1)}


def test_every_clause_is_covered_by_a_test_that_exists() -> None:
    for clause, tests in CLAUSES.items():
        assert tests, clause
        for reference in tests:
            path, name = reference.split("::")
            defined = {
                node.name
                for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8")))
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            assert name in defined, f"{clause}: {reference}"


@pytest_asyncio.fixture
async def env() -> AsyncIterator[tuple[AsyncConnection, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable test database")
    url = DatabaseConfig.from_environment().database_url.get_secret_value()
    schema = "cme_replay_" + uuid4().hex[:16]
    async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=5) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET timezone = 'UTC'")
        await apply_migrations(conn)
        await conn.execute("INSERT INTO predicate_schema VALUES ('fact', 'multi', 'semantic')")
        await conn.execute("SELECT set_config('app.user_id', 'u1', false)")
        await conn.execute("INSERT INTO app_user VALUES ('u1', 'ext-u1', %s)", (T0,))
        await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s1', 'u1', %s)", (T0,))
        for sequence, (turn, at) in enumerate(TURN_TIMES.items(), start=1):
            await conn.execute(
                "INSERT INTO turn VALUES (%s, 's1', 'u1', %s, 'user', %s, %s)", (turn, sequence, f"fact {turn}", at)
            )
        try:
            yield conn, schema
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _job(turn: str, *, job_id: str | None = None) -> NewJob:
    """The extraction identity is (user, turn, extractor version, candidate fingerprint), as in Invariant 9."""
    return NewJob(
        id=job_id or f"job-{turn}",
        user_id="u1",
        kind="extract",
        idempotency_key=f"extract:{turn}" if job_id is None else f"replay:{job_id}",
        turn_id=turn,
        extractor_version="extractor-v1",
        candidate_fingerprint=f"fp-{turn}",
        payload={"turn": turn},
    )


async def _extract(conn: AsyncConnection, job: Job, tick: ClockTick, schema: str) -> None:
    assert job.turn_id is not None
    await commit_initial_version(
        conn,
        CandidateVersionInput(
            version_id=f"v-{job.turn_id}",
            memory_key=f"k-{job.turn_id}",
            user_id="u1",
            subject_key="user",
            predicate_key="fact",
            single_valued=False,
            content=f"fact {job.turn_id}",
            valid_from=TURN_TIMES[job.turn_id],
            evidence=(EvidenceInput(turn_id=job.turn_id, evidence_span=f"fact {job.turn_id}"),),
        ),
        schema_name=schema,
        clock_tick=tick,
    )


async def _drain(conn: AsyncConnection, schema: str) -> list[ClockTick]:
    ticks: list[ClockTick] = []

    async def handler(c: AsyncConnection, job: Job, tick: ClockTick) -> None:
        await _extract(c, job, tick, schema)

    while True:
        cursor = await conn.execute("SELECT turn_id FROM job WHERE status = 'queued' ORDER BY next_run_at, id LIMIT 1")
        row = await cursor.fetchone()
        replay = TURN_TIMES[row[0]] if row else T0
        result = await process_one(conn, "u1", handler, schema_name=schema, replay_time=replay)
        if result.status == "idle":
            return ticks
        assert result.status == "done" and result.clock is not None
        ticks.append(result.clock)


async def _versions(conn: AsyncConnection) -> list[tuple[str, datetime]]:
    cursor = await conn.execute("SELECT id, lower(recorded_range) FROM memory_version ORDER BY lower(recorded_range)")
    return [(row[0], row[1]) for row in await cursor.fetchall()]


async def test_end_to_end_replay_is_monotone_and_never_duplicates(env: tuple[AsyncConnection, str]) -> None:
    conn, schema = env
    for turn in TURN_TIMES:
        assert (await enqueue(conn, _job(turn), schema_name=schema)).created
    ticks = await _drain(conn, schema)
    first = await _versions(conn)
    assert len(ticks) == len(first) == len(TURN_TIMES)
    instants = [tick.instant for tick in ticks]
    assert instants == sorted(instants) and len(set(instants)) == len(instants), "strictly increasing clock"
    assert any(tick.advanced for tick in ticks), "the tie and the backwards step were advanced, not reused"
    assert [instant for _, instant in first] == instants

    # Replay 1: the identical jobs again (same ids and keys) are recognised, not re-run.
    for turn in TURN_TIMES:
        assert not (await enqueue(conn, _job(turn), schema_name=schema)).created
    assert await _drain(conn, schema) == []
    # Replay 2: a fresh replay run with new job ids/keys but the same extraction identity is still one job each.
    for turn in TURN_TIMES:
        assert not (await enqueue(conn, _job(turn, job_id=f"rerun-{turn}"), schema_name=schema)).created
    assert await _drain(conn, schema) == []

    assert await _versions(conn) == first, "zero duplicate memory versions after two full replays"
    cursor = await conn.execute("SELECT count(*), count(DISTINCT turn_id) FROM job")
    assert await cursor.fetchone() == (len(TURN_TIMES), len(TURN_TIMES))
