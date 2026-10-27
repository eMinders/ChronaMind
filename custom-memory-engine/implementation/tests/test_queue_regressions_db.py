"""Adversarial atomic-completion, pinned-identity and commit-retry boundaries."""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import aclosing
from typing import Protocol, cast

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.types.json import Jsonb

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from implementation.clock import ClockTick
from implementation.queue import Job, QueueError, process_one, worker
from implementation.tests.test_clock_queue_db import (
    BASE,
    connect,
    identity,
    new_job,
    scalar,
    submit,
    write_memory,
)
from implementation.tests.test_clock_queue_db import (
    database as canonical_database,
)

pytestmark = pytest.mark.integration


class CanonicalFixture(Protocol):
    __wrapped__: Callable[[], AsyncGenerator[tuple[DatabaseConfig, str], None]]


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    factory = cast(CanonicalFixture, canonical_database).__wrapped__
    async with aclosing(factory()) as fixtures:
        yield await anext(fixtures)


async def test_handler_cannot_commit_memory_after_deleting_its_queue_completion_row(database) -> None:
    _, schema = database
    await submit(database, new_job("deleted-completion"))

    async def deletes_job(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        await write_memory(conn, job, tick)
        await conn.execute("DELETE FROM job WHERE id=%s", (job.id,))

    async with connect(database) as conn:
        await identity(conn, "u1")
        result = await process_one(conn, "u1", deletes_job, schema_name=schema, replay_time=BASE)
        assert result.status == "dead_letter", "memory effects require a durable matching job completion row"
        assert await scalar(conn, "SELECT status FROM job WHERE id='deleted-completion'") == "dead_letter"
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0
        assert await scalar(conn, "SELECT count(*) FROM memory_evidence") == 0
        assert await scalar(conn, "SELECT count(*) FROM audit_event WHERE event_type='clock_allocated'") == 0


@pytest.mark.parametrize("change", ["payload_json_type", "kind"])
async def test_retry_or_failure_finalizer_cannot_consume_changed_pinned_work(database, monkeypatch, change) -> None:
    _, schema = database
    await submit(database, new_job("pinned-change", payload={"x": True}))
    real_retrying = worker.AsyncRetrying
    modifications = []
    calls = []

    async def change_work_during_backoff(delay) -> None:
        assert 0 <= float(delay) <= 2
        assert not modifications, "the controlled identity change must happen once after the first rollback"
        async with connect(database) as other:
            await identity(other, "u1")
            if change == "payload_json_type":
                # Python True == 1; PostgreSQL JSONB true and number1 differ.
                await other.execute("UPDATE job SET payload=%s WHERE id='pinned-change'", (Jsonb({"x": 1}),))
            else:
                await other.execute("UPDATE job SET kind='changed-work' WHERE id='pinned-change'")
        modifications.append(change)

    def retrying_with_controlled_backoff(**kwargs):
        kwargs["sleep"] = change_work_during_backoff
        return real_retrying(**kwargs)

    monkeypatch.setattr(worker, "AsyncRetrying", retrying_with_controlled_backoff)

    async def fails_once(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        calls.append(job.id)
        await write_memory(conn, job, tick)
        if len(calls) == 1:
            await conn.execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic retry' USING ERRCODE='40001'; END $$")

    async with connect(database) as conn:
        await identity(conn, "u1")
        with pytest.raises(QueueError, match="pinned_job_identity_changed"):
            await process_one(conn, "u1", fails_once, schema_name=schema, replay_time=BASE)
        assert modifications == [change] and calls == ["pinned-change"]
        assert await scalar(conn, "SELECT status FROM job WHERE id='pinned-change'") == "queued"
        assert await scalar(conn, "SELECT attempts FROM job WHERE id='pinned-change'") == 0
        if change == "payload_json_type":
            assert await scalar(conn, "SELECT jsonb_typeof(payload->'x') FROM job") == "number"
        else:
            assert await scalar(conn, "SELECT kind FROM job WHERE id='pinned-change'") == "changed-work"
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0
        assert await scalar(conn, "SELECT count(*) FROM memory_evidence") == 0
        assert await scalar(conn, "SELECT count(*) FROM audit_event") == 0


async def test_deferred_commit_conflicts_retry_the_whole_transaction_without_duplicate_effects(database) -> None:
    _, schema = database
    await submit(database, new_job("commit-retry"))
    transactions = []
    ticks = []
    async with connect(database) as conn:
        await conn.execute(sql.SQL("CREATE SEQUENCE {}.commit_attempts").format(sql.Identifier(schema)))
        await conn.execute(
            sql.SQL(
                """CREATE FUNCTION {}.fail_first_commits() RETURNS trigger LANGUAGE plpgsql AS $body$
                   BEGIN
                     IF pg_catalog.nextval({}) <= 2 THEN
                       RAISE EXCEPTION 'synthetic_commit_conflict' USING ERRCODE='40001';
                     END IF;
                     RETURN NEW;
                   END
                   $body$"""
            ).format(sql.Identifier(schema), sql.Literal(schema + ".commit_attempts"))
        )
        await conn.execute(
            sql.SQL(
                """CREATE CONSTRAINT TRIGGER zz_commit_retry AFTER INSERT ON {}.memory_version
                   DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                   EXECUTE FUNCTION {}.fail_first_commits()"""
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        await identity(conn, "u1")

        async def successful_body(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
            transactions.append(await scalar(conn, "SELECT pg_current_xact_id()::text"))
            ticks.append(tick.instant)
            await write_memory(conn, job, tick)

        result = await process_one(conn, "u1", successful_body, schema_name=schema, replay_time=BASE)
        assert result.status == "done" and result.job_id == "commit-retry"
        assert result.attempts == result.transaction_attempts == 3
        assert len(transactions) == len(set(transactions)) == 3 and ticks == [BASE, BASE, BASE]
        assert await scalar(conn, "SELECT last_value FROM commit_attempts") == 3
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 1
        assert await scalar(conn, "SELECT count(*) FROM memory_evidence") == 1
        assert await scalar(conn, "SELECT count(*) FROM audit_event WHERE event_type='clock_allocated'") == 1
        assert await scalar(conn, "SELECT status FROM job WHERE id='commit-retry'") == "done"


@pytest.mark.parametrize("prior_attempts,expected_calls", [(2, 1), (3, 0)])
async def test_persisted_attempts_are_part_of_the_fixed_job_retry_budget(
    database, prior_attempts, expected_calls
) -> None:
    _, schema = database
    await submit(database, new_job("persisted-budget"))
    calls = []
    async with connect(database) as conn:
        await identity(conn, "u1")
        await conn.execute("UPDATE job SET attempts=%s WHERE id='persisted-budget'", (prior_attempts,))

        async def transient(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
            calls.append(job.id)
            await write_memory(conn, job, tick)
            await conn.execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic retry' USING ERRCODE='40001'; END $$")

        result = await process_one(conn, "u1", transient, schema_name=schema, replay_time=BASE)
        assert result.status == "dead_letter" and result.attempts == 3
        assert len(calls) == expected_calls
        assert await scalar(conn, "SELECT attempts FROM job WHERE id='persisted-budget'") == 3
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0
        assert await scalar(conn, "SELECT count(*) FROM audit_event") == 0
