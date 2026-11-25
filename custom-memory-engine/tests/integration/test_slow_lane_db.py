"""The asynchronous slow lane against the real queue (``E-047``).

What these tests exist to prove, and why each one is not redundant with the ingestor's own suite:

* the queue's **second identity** makes re-enqueueing the same turn a no-op and a new extractor version a new job;
* a claim takes the row out of the queue and leaves **no transaction open** afterwards (the property that lets a model
  call happen without holding locks);
* a completed job writes its claims and is marked ``done`` with its payload cleared, so a finished job keeps no copy of
  anything derived from the turn;
* a failing job backs off and then dead-letters exactly at ``MAX_ATTEMPTS`` — a broken endpoint must not become an
  infinite retry loop;
* a crashed worker's lease is recovered, which is the only state the two-transaction shape introduces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import AsyncConnection
from psycopg import sql as pg_sql
from psycopg.errors import ForeignKeyViolation

from custom_memory_engine.engine.ingest import TurnInput
from custom_memory_engine.queue.contracts import QueueError
from custom_memory_engine.queue.worker import MAX_ATTEMPTS, enqueue
from custom_memory_engine.semantic.contracts import ExtractedClaim, ExtractionResult
from custom_memory_engine.semantic.ingestor import apply_extraction
from custom_memory_engine.semantic.slow_lane import claim_one, complete_one, enqueue_slow_lane, recover_stale
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
CLAIM = ExtractedClaim(
    content="The user lives in Berlin",
    verbatim_quote="I live in Berlin",
    cardinality="single",
    semantic_slot="residence",
)
TURN = TurnInput(
    turn_id="t-alice",
    user_id="alice",
    session_id="s-alice",
    sequence_no=2,
    role="user",
    content="I live in Berlin",
    occurred_at=T0,
)


@asynccontextmanager
async def world(database) -> AsyncIterator[tuple[AsyncConnection, str]]:  # noqa: F811
    config, schema = database
    async with connect(database) as conn:
        await conn.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (T0,))
        await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', %s)", (T0,))
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES ('t-alice', 's-alice', 'alice', 2, 'user', 'I live in Berlin', %s)",
            (T0,),
        )
        # the queue's writers check that the connection's tenant is the job's tenant
        await conn.execute("SELECT set_config('app.user_id', 'alice', false)")
        await conn.commit()
        yield conn, schema


async def enqueue_one(conn: AsyncConnection, schema: str, *, version: str = "extract-v1", key: str = "j-1") -> None:
    await enqueue(
        conn,
        enqueue_slow_lane(
            job_id=key,
            user_id="alice",
            turn_id="t-alice",
            extractor_version=version,
            candidate_fingerprint="f" * 32,
        ),
        schema_name=schema,
    )


async def job_rows(conn: AsyncConnection, schema: str) -> list[tuple[Any, ...]]:
    return await rows(conn, f"SELECT id, status, attempts FROM {schema}.job ORDER BY id")


# ----------------------------------------------------------------------------------------------- the queue identity


async def test_re_enqueueing_the_same_turn_is_a_noop_and_a_new_extractor_version_is_new_work(database) -> None:  # noqa: F811
    async with world(database) as (conn, schema):
        first = enqueue_slow_lane(
            job_id="j-1", user_id="alice", turn_id="t-alice", extractor_version="v1", candidate_fingerprint="f" * 32
        )
        again = enqueue_slow_lane(
            job_id="j-2", user_id="alice", turn_id="t-alice", extractor_version="v1", candidate_fingerprint="f" * 32
        )
        await enqueue(conn, first, schema_name=schema)  # type: ignore[arg-type]
        duplicate = await enqueue(conn, again, schema_name=schema)  # type: ignore[arg-type]
        assert duplicate.created is False and duplicate.job.id == "j-1"

        newer = enqueue_slow_lane(
            job_id="j-3", user_id="alice", turn_id="t-alice", extractor_version="v2", candidate_fingerprint="f" * 32
        )
        created = await enqueue(conn, newer, schema_name=schema)  # type: ignore[arg-type]
        assert created.created is True
        assert [row[:2] for row in await job_rows(conn, schema)] == [("j-1", "queued"), ("j-3", "queued")]


# -------------------------------------------------------------------------------------------- claim / complete


async def test_a_claim_takes_the_job_out_of_the_queue_and_opens_no_transaction(database) -> None:  # noqa: F811
    async with world(database) as (conn, schema):
        await enqueue_one(conn, schema)
        job = await claim_one(conn, "alice", schema_name=schema)
        assert job is not None and job.status == "running" and job.attempts == 1
        # the connection is idle again: this is the property that lets the model call happen outside a transaction
        assert conn.info.transaction_status.name == "IDLE"
        assert await job_rows(conn, schema) == [("j-1", "running", 1)]
        # and a second claim sees nothing, because the first one holds it
        assert await claim_one(conn, "alice", schema_name=schema) is None


async def test_a_completed_job_writes_its_claims_and_forgets_its_payload(database) -> None:  # noqa: F811
    async with world(database) as (conn, schema):
        await enqueue_one(conn, schema)
        job = await claim_one(conn, "alice", schema_name=schema)
        assert job is not None

        async def work(connection: AsyncConnection, _job: Any, tick: Any) -> None:
            # the tick `complete_one` allocated is passed through: one belief instant per transaction, and a second
            # allocation raises (`clock_already_allocated_in_transaction`)
            await apply_extraction(
                connection,
                user_id="alice",
                scope_path="/u/alice/",
                turn=TURN,
                episode_id=None,
                extraction=ExtractionResult(claims=(CLAIM,), extractor_version="v1"),
                schema_name=schema,
                clock_tick=tick,
            )

        result = await complete_one(conn, "alice", job, work, schema_name=schema)
        assert result.status == "done" and result.attempts == 1
        written = await rows(conn, f"SELECT content FROM {schema}.memory_version WHERE kind = 'claim'")
        assert written == [("The user lives in Berlin",)]
        stored = await rows(conn, f"SELECT status, payload FROM {schema}.job")
        assert stored == [("done", None)]


async def test_a_failing_job_backs_off_then_dead_letters_exactly_once(database) -> None:  # noqa: F811
    """A broken endpoint must not become an infinite loop: `MAX_ATTEMPTS`, then `dead_letter`, and no claims."""
    async with world(database) as (conn, schema):
        await enqueue_one(conn, schema)

        async def boom(connection: AsyncConnection, _job: Any, tick: Any) -> None:
            del connection, tick
            raise RuntimeError("model down")

        statuses: list[str] = []
        for _ in range(MAX_ATTEMPTS + 1):
            job = await claim_one(conn, "alice", schema_name=schema)
            if job is None:
                # the backoff has not elapsed; force it so the loop measures the policy and not the clock
                await conn.execute(
                    pg_sql.SQL("UPDATE {} SET next_run_at = pg_catalog.now()").format(pg_sql.Identifier(schema, "job"))
                )
                job = await claim_one(conn, "alice", schema_name=schema)
            if job is None:
                break
            result = await complete_one(conn, "alice", job, boom, schema_name=schema)
            statuses.append(result.status)
        assert statuses == ["idle", "idle", "dead_letter"]
        assert await job_rows(conn, schema) == [("j-1", "dead_letter", MAX_ATTEMPTS)]
        assert await rows(conn, f"SELECT COUNT(*) FROM {schema}.memory_version") == [(0,)]


async def test_a_crashed_workers_lease_is_recovered(database) -> None:  # noqa: F811
    """The only state the two-transaction shape introduces: `running` with nobody running it."""
    async with world(database) as (conn, schema):
        await enqueue_one(conn, schema)
        job = await claim_one(conn, "alice", schema_name=schema)
        assert job is not None
        # the worker dies here: nothing completes the job, and its lease runs out
        await conn.execute(
            pg_sql.SQL("UPDATE {} SET next_run_at = pg_catalog.now() - interval '1 hour', status='running'").format(
                pg_sql.Identifier(schema, "job")
            )
        )
        assert await recover_stale(conn, "alice", schema_name=schema) == 1
        reclaimed = await claim_one(conn, "alice", schema_name=schema)
        assert reclaimed is not None and reclaimed.attempts == 2  # the retry is counted, so a poison job still dies


async def test_the_claim_is_tenant_scoped(database) -> None:  # noqa: F811
    """A worker serving bob cannot take alice's job: the queue is under her row-level security."""
    async with world(database) as (conn, schema):
        await conn.execute("INSERT INTO app_user VALUES ('bob', 'ext-bob', %s)", (T0,))
        await enqueue_one(conn, schema)
        assert await claim_one(conn, "bob", schema_name=schema) is None
        assert (await job_rows(conn, schema))[0][1] == "queued"


async def test_claiming_requires_an_idle_connection(database) -> None:  # noqa: F811
    """A claim inside somebody else's transaction would inherit its locks and its rollback."""
    async with world(database) as (conn, schema):
        await enqueue_one(conn, schema)
        async with conn.transaction():
            with pytest.raises(QueueError) as refused:
                await claim_one(conn, "alice", schema_name=schema)
            assert "claim_requires_idle_connection" in str(refused.value)


async def test_enqueueing_for_a_turn_that_does_not_exist_is_refused(database) -> None:  # noqa: F811
    async with world(database) as (conn, schema):
        with pytest.raises(ForeignKeyViolation):  # `turn_id` references `turn`; a job cannot outlive its evidence
            await enqueue(
                conn,
                enqueue_slow_lane(
                    job_id="j-x",
                    user_id="alice",
                    turn_id="no-such-turn",
                    extractor_version="v1",
                    candidate_fingerprint="f" * 32,
                ),
                schema_name=schema,
            )
