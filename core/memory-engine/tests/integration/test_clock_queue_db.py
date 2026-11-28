"""RL-044 native clock, queue, transaction and concurrency contracts."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import LiteralString
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, pq, sql
from psycopg.errors import InsufficientPrivilege

from custom_memory_engine.clock import ClockError, ClockTick, allocate_clock
from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.queue import Job, JobIdentityConflict, NewJob, QueueError, enqueue, process_one
from tests.integration.schema import engine_database

pytestmark = pytest.mark.integration

CME_ROOT = Path(__file__).resolve().parents[2]
BASE = datetime(2026, 3, 10, tzinfo=UTC)
LATER = datetime(2026, 8, 20, tzinfo=UTC)
TICK = timedelta(microseconds=1)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    async with engine_database() as (config, schema):
        async with connect((config, schema)) as conn:
            await conn.execute(
                "INSERT INTO predicate_schema VALUES ('fact','multi','semantic'),('state','single','semantic')"
            )
            for user in ("u1", "u2"):
                await identity(conn, user)
                await conn.execute("INSERT INTO app_user VALUES (%s,%s,%s)", (user, "external-" + user, BASE))
                await conn.execute(
                    "INSERT INTO session (id,user_id,started_at) VALUES (%s,%s,%s)",
                    ("session-" + user, user, BASE),
                )
                await conn.execute(
                    """INSERT INTO turn (id,session_id,user_id,sequence_no,role,content,occurred_at)
                           VALUES (%s,%s,%s,1,'user','synthetic supporting user statement',%s)""",
                    ("turn-" + user, "session-" + user, user, BASE),
                )
        yield config, schema


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET statement_timeout = '10s'")
        await conn.execute("SET timezone = 'UTC'")
        yield conn


async def identity(conn: AsyncConnection, user: str) -> None:
    await conn.execute("SELECT set_config('app.user_id',%s,false)", (user,))


async def scalar(conn: AsyncConnection, statement: LiteralString, parameters=()):
    cursor = await conn.execute(statement, parameters)
    row = await cursor.fetchone()
    assert row is not None
    return row[0]


async def allocate(database: tuple[DatabaseConfig, str], user: str, raw: datetime = BASE) -> ClockTick:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, user)
        async with conn.transaction():
            tick = await allocate_clock(conn, user, schema_name=schema, replay_time=raw)
            cursor = await conn.execute(sql.SQL("SELECT {}.clock_now()").format(sql.Identifier(schema)))
            assert await cursor.fetchone() == (tick.instant,)
        return tick


def new_job(job_id: str, user: str = "u1", **changes: object) -> NewJob:
    data: dict[str, object] = dict(
        id=job_id,
        user_id=user,
        kind="write",
        idempotency_key="key-" + job_id,
        turn_id="turn-" + user,
        extractor_version="extractor-v1",
        candidate_fingerprint="candidate-" + job_id,
        payload={"value": job_id},
    )
    data.update(changes)
    return NewJob.model_validate(data)


async def submit(database: tuple[DatabaseConfig, str], job: NewJob):
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, job.user_id)
        return await enqueue(conn, job, schema_name=schema)


async def write_memory(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
    version_id = "version-" + job.id
    await conn.execute(
        """INSERT INTO memory_version (
               id,memory_key,user_id,status,subject_key,predicate_key,single_valued,content,event_range,recorded_range
           ) VALUES (%s,%s,%s,'active','self','fact',false,'synthetic committed fact',
                     tstzrange(%s,NULL,'[)'),tstzrange(clock_now(),NULL,'[)'))""",
        (version_id, job.id, job.user_id, BASE),
    )
    await conn.execute(
        """INSERT INTO memory_evidence (version_id,turn_id,user_id,evidence_span,evidence_role)
           VALUES (%s,%s,%s,'synthetic supporting user statement','support')""",
        (version_id, job.turn_id, job.user_id),
    )
    # the gate decision that admitted it (migration 14 requires it of the application role)
    await conn.execute(
        """INSERT INTO memory_conflict
               (id,candidate_version_id,user_id,relation,decision,reason_code,decided_by,decided_at)
           VALUES (%s,%s,%s,'unrelated','accept','gate_passed','validation-gate',clock_now())""",
        ("conflict-" + job.id, version_id, job.user_id),
    )
    assert await scalar(conn, "SELECT lower(recorded_range) FROM memory_version WHERE id=%s", (version_id,)) == (
        tick.instant
    )


async def wait_for_advisory_wait(conn: AsyncConnection, pid: int) -> None:
    async with asyncio.timeout(5):
        # The event is in PostgreSQL, so observe its actual lock state rather
        # than inferring blocking from an arbitrary fixed sleep.
        for _ in range(500):
            if await scalar(
                conn,
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted)",
                (pid,),
            ):
                return
            await asyncio.sleep(0.01)
        pytest.fail("the expected PostgreSQL advisory-lock wait was not observed")


async def test_replay_watermark_survives_connections_ties_and_out_of_order_without_memory_rows(database) -> None:
    first = await allocate(database, "u1")
    tied = await allocate(database, "u1")
    older = await allocate(database, "u1", BASE - timedelta(days=1))
    different_user = await allocate(database, "u2")
    assert (first.instant, tied.instant, older.instant, different_user.instant) == (
        BASE,
        BASE + TICK,
        BASE + 2 * TICK,
        BASE,
    )
    assert first.mode == tied.mode == older.mode == "replay"
    assert not first.advanced and tied.advanced and older.advanced
    assert older.raw_instant == BASE - timedelta(days=1)
    async with connect(database) as conn:
        await identity(conn, "u1")
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0
        cursor = await conn.execute("SELECT id,memory_ref,details FROM audit_event WHERE user_id='u1'")
        for event_id, memory_ref, details in await cursor.fetchall():
            assert UUID(event_id) and UUID(memory_ref)
            assert set(details) == {"clock_mode", "transaction_id"}
            assert details["clock_mode"] == "replay"
            assert details["transaction_id"].isdigit()


async def test_replay_watermark_is_read_by_a_fresh_python_process(database) -> None:
    config, schema = database
    assert (await allocate(database, "u1")).instant == BASE
    program = """
import asyncio,json,os,sys
from datetime import datetime
from psycopg import AsyncConnection,sql
from custom_memory_engine.clock import allocate_clock
async def main():
    async with await AsyncConnection.connect(os.environ['CME_TEST_DATABASE_URL'],autocommit=True) as conn:
        await conn.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(sys.argv[1])))
        await conn.execute("SELECT set_config('app.user_id','u1',false)")
        async with conn.transaction():
            tick=await allocate_clock(conn,'u1',schema_name=sys.argv[1],
                                      replay_time=datetime.fromisoformat(sys.argv[2]))
            print(json.dumps({'instant':tick.instant.isoformat(),'mode':tick.mode}),flush=True)
if sys.platform=='win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    }
    environment.update(CME_TEST_DATABASE_URL=config.database_url.get_secret_value(), PYTHONUTF8="1")
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", program, schema, BASE.isoformat()],
        cwd=CME_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "fresh_process_clock_allocation_failed"
    assert json.loads(result.stdout) == {"instant": (BASE + TICK).isoformat(), "mode": "replay"}


async def test_live_clock_uses_transaction_timestamp_and_allocates_only_once(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        await conn.execute("SELECT set_config('app.clock',%s,false)", (BASE.isoformat(),))
        async with conn.transaction():
            raw = await scalar(conn, "SELECT transaction_timestamp()")
            tick = await allocate_clock(conn, "u1", schema_name=schema)
            assert tick.raw_instant == tick.instant == raw and tick.mode == "live" and not tick.advanced
            with pytest.raises(ClockError, match="clock_already_allocated_in_transaction"):
                await allocate_clock(conn, "u1", schema_name=schema)
            cursor = await conn.execute(sql.SQL("SELECT {}.clock_now()").format(sql.Identifier(schema)))
            assert await cursor.fetchone() == (tick.instant,)
            assert await scalar(conn, "SELECT count(*) FROM audit_event") == 1
        assert await scalar(conn, "SELECT current_setting('app.clock')::timestamptz") == BASE


async def test_rollback_discards_clock_allocation_and_restores_the_pooled_clock(database) -> None:
    _, schema = database
    assert (await allocate(database, "u1")).instant == BASE
    async with connect(database) as conn:
        await identity(conn, "u1")
        await conn.execute("SELECT set_config('app.clock',%s,false)", (LATER.isoformat(),))
        with pytest.raises(RuntimeError, match="rollback-clock-owner"):
            async with conn.transaction():
                tick = await allocate_clock(conn, "u1", schema_name=schema, replay_time=BASE)
                assert tick.instant == BASE + TICK
                raise RuntimeError("rollback-clock-owner")
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
        assert await scalar(conn, "SELECT current_setting('app.clock')::timestamptz") == LATER
        assert await scalar(conn, "SELECT count(*) FROM audit_event WHERE user_id='u1'") == 1
    assert (await allocate(database, "u1")).instant == BASE + TICK


async def test_existing_memory_recorded_lower_and_closed_upper_constrain_new_clock(database) -> None:
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO memory_version (
                       id,memory_key,user_id,status,subject_key,predicate_key,single_valued,content,
                       event_range,recorded_range
                   ) VALUES ('old','memory','u1','active','self','fact',false,'synthetic',
                             tstzrange(%s,NULL,'[)'),tstzrange(%s,%s,'[)'))""",
                (BASE, BASE, LATER),
            )
            await conn.execute(
                """INSERT INTO memory_evidence VALUES
                   ('old','turn-u1','u1','synthetic supporting user statement','support')"""
            )
    assert (await allocate(database, "u1", BASE)).instant == LATER + TICK


async def test_clock_refuses_unscoped_idle_and_non_read_committed_transactions(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        with pytest.raises(ClockError, match="clock_active_transaction_required"):
            await allocate_clock(conn, "u1", schema_name=schema, replay_time=BASE)
        await identity(conn, "u2")
        async with conn.transaction():
            with pytest.raises(InsufficientPrivilege):
                await allocate_clock(conn, "u1", schema_name=schema, replay_time=BASE)
        await identity(conn, "u1")
        async with conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            with pytest.raises(ClockError, match="clock_read_committed_required"):
                await allocate_clock(conn, "u1", schema_name=schema, replay_time=BASE)
        assert await scalar(conn, "SELECT count(*) FROM audit_event") == 0


async def test_clock_serializes_same_user_while_other_user_progresses(database) -> None:
    _, schema = database
    async with connect(database) as holder, connect(database) as waiter, connect(database) as observer:
        await identity(holder, "u1")
        await identity(waiter, "u1")
        waiter_pid = await scalar(waiter, "SELECT pg_backend_pid()")
        started = asyncio.Event()

        async def waiting_allocation() -> ClockTick:
            async with waiter.transaction():
                started.set()
                return await allocate_clock(waiter, "u1", schema_name=schema, replay_time=BASE)

        task = None
        try:
            async with holder.transaction():
                first = await allocate_clock(holder, "u1", schema_name=schema, replay_time=BASE)
                task = asyncio.create_task(waiting_allocation())
                await started.wait()
                await wait_for_advisory_wait(observer, waiter_pid)
                assert not task.done()
                assert (await asyncio.wait_for(allocate(database, "u2"), timeout=5)).instant == BASE
            assert (await asyncio.wait_for(task, timeout=5)).instant == first.instant + TICK
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_enqueue_deduplicates_both_durable_keys_without_resetting_completed_work(database) -> None:
    _, schema = database
    request = new_job("one")
    first = await submit(database, request)
    assert first.created and first.job.status == "queued"
    same = await submit(database, request)
    assert not same.created and same.job.id == "one"
    extraction_retry = new_job(
        "another-id", candidate_fingerprint=request.candidate_fingerprint, payload=request.payload
    )
    duplicate = await submit(database, extraction_retry)
    assert not duplicate.created and duplicate.job.id == "one"
    calls = []

    async def handler(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        calls.append(job.id)
        await write_memory(conn, job, tick)

    async with connect(database) as conn:
        await identity(conn, "u1")
        result = await process_one(conn, "u1", handler, schema_name=schema, replay_time=BASE)
        assert result.status == "done" and result.job_id == "one" and result.attempts == 1
        again = await enqueue(conn, request, schema_name=schema)
        assert not again.created and again.job.status == "done" and again.job.attempts == 1
        assert (await process_one(conn, "u1", handler, schema_name=schema, replay_time=BASE)).status == "idle"
        assert calls == ["one"] and await scalar(conn, "SELECT count(*) FROM memory_version") == 1


async def test_concurrent_enqueue_and_changed_identity_are_not_duplicate_or_overwritten(database) -> None:
    _, schema = database
    request = new_job("concurrent")
    first, second = await asyncio.gather(submit(database, request), submit(database, request))
    assert sorted((first.created, second.created)) == [False, True]
    async with connect(database) as conn:
        await identity(conn, "u1")
        with pytest.raises(JobIdentityConflict) as changed:
            await enqueue(
                conn, new_job("concurrent", payload={"value": "different private content"}), schema_name=schema
            )
        assert changed.value.sqlstate == "23505"
        assert "different private content" not in str(changed.value)
        assert await scalar(conn, "SELECT count(*) FROM job") == 1


async def test_job_and_clock_changes_remain_inside_caller_enqueue_transaction(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        with pytest.raises(RuntimeError, match="enqueue-owner-rollback"):
            async with conn.transaction():
                assert (await enqueue(conn, new_job("rollback"), schema_name=schema)).created
                assert conn.info.transaction_status == pq.TransactionStatus.INTRANS
                raise RuntimeError("enqueue-owner-rollback")
        assert await scalar(conn, "SELECT count(*) FROM job") == 0


async def test_future_due_job_is_not_claimed_or_given_a_clock(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        now = await scalar(conn, "SELECT transaction_timestamp()")
        await enqueue(conn, new_job("future", not_before=now + timedelta(days=1)), schema_name=schema)

        async def unexpected_handler(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
            pytest.fail("a future job must not enter the mutation handler")

        assert (
            await process_one(conn, "u1", unexpected_handler, schema_name=schema, replay_time=BASE)
        ).status == "idle"
        assert await scalar(conn, "SELECT count(*) FROM audit_event") == 0
        assert await scalar(conn, "SELECT attempts FROM job WHERE id='future'") == 0


async def test_skip_locked_claims_the_next_due_job_without_waiting_for_locked_row(database) -> None:
    _, schema = database
    for job_id in ("a-first", "b-second"):
        await submit(database, new_job(job_id, not_before=BASE))
    async with connect(database) as holder, connect(database) as worker:
        await identity(holder, "u1")
        await identity(worker, "u1")
        async with holder.transaction():
            await holder.execute("SELECT id FROM job WHERE id='a-first' FOR UPDATE")
            result = await asyncio.wait_for(
                process_one(worker, "u1", write_memory, schema_name=schema, replay_time=BASE), timeout=5
            )
            assert result.status == "done" and result.job_id == "b-second"
        result = await process_one(worker, "u1", write_memory, schema_name=schema, replay_time=BASE)
        assert result.status == "done" and result.job_id == "a-first"
        assert await scalar(worker, "SELECT count(*) FROM memory_version") == 2


async def test_two_workers_serialize_same_user_handlers_and_commit_each_job_once(database) -> None:
    _, schema = database
    for job_id in ("a-first", "b-second"):
        await submit(database, new_job(job_id, not_before=BASE))
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    ticks = []

    async def blocking_handler(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        calls.append(job.id)
        ticks.append(tick.instant)
        await write_memory(conn, job, tick)
        if job.id == "a-first":
            entered.set()
            await release.wait()

    async with connect(database) as first, connect(database) as second, connect(database) as observer:
        await identity(first, "u1")
        await identity(second, "u1")
        pid = await scalar(second, "SELECT pg_backend_pid()")
        tasks = []
        try:
            tasks.append(
                asyncio.create_task(process_one(first, "u1", blocking_handler, schema_name=schema, replay_time=BASE))
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            tasks.append(
                asyncio.create_task(process_one(second, "u1", blocking_handler, schema_name=schema, replay_time=BASE))
            )
            await wait_for_advisory_wait(observer, pid)
            assert calls == ["a-first"] and not tasks[1].done()
            assert await scalar(observer, "SELECT status FROM job WHERE id='b-second'") == "queued"
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
            assert all(result.status == "done" for result in results)
            assert calls == ["a-first", "b-second"] and ticks == [BASE, BASE + TICK]
            assert await scalar(observer, "SELECT count(*) FROM memory_version") == 2
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_other_user_worker_progresses_while_one_users_handler_is_blocked(database) -> None:
    _, schema = database
    await submit(database, new_job("held", "u1"))
    await submit(database, new_job("free", "u2"))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        await write_memory(conn, job, tick)
        entered.set()
        await release.wait()

    async with connect(database) as first, connect(database) as second:
        await identity(first, "u1")
        await identity(second, "u2")
        task = asyncio.create_task(process_one(first, "u1", blocked, schema_name=schema, replay_time=BASE))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            other = await asyncio.wait_for(
                process_one(second, "u2", write_memory, schema_name=schema, replay_time=BASE), timeout=5
            )
            assert other.status == "done" and other.job_id == "free" and not task.done()
            release.set()
            assert (await asyncio.wait_for(task, timeout=5)).status == "done"
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
async def test_transient_failures_retry_fresh_whole_transactions_with_one_committed_memory(database, sqlstate) -> None:
    _, schema = database
    await submit(database, new_job("retry"))
    transactions = []
    instants = []

    async def transient(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        transactions.append(await scalar(conn, "SELECT pg_current_xact_id()::text"))
        instants.append(tick.instant)
        await write_memory(conn, job, tick)
        if len(transactions) < 3:
            await conn.execute(
                sql.SQL("DO $$ BEGIN RAISE EXCEPTION 'synthetic transient' USING ERRCODE={}; END $$").format(
                    sql.Literal(sqlstate)
                )
            )

    async with connect(database) as conn:
        await identity(conn, "u1")
        result = await process_one(conn, "u1", transient, schema_name=schema, replay_time=BASE)
        assert result.status == "done" and result.attempts == result.transaction_attempts == 3
        assert len(set(transactions)) == 3 and instants == [BASE, BASE, BASE]
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 1
        assert await scalar(conn, "SELECT count(*) FROM audit_event WHERE event_type='clock_allocated'") == 1
        assert await scalar(conn, "SELECT attempts FROM job WHERE id='retry'") == 3
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE


async def test_retry_pins_the_original_job_when_another_worker_completes_it(database) -> None:
    _, schema = database
    for job_id in ("a-first", "b-second"):
        await submit(database, new_job(job_id, not_before=BASE))
    first_entered = asyncio.Event()
    raise_transient = asyncio.Event()
    second_entered = asyncio.Event()
    finish_second = asyncio.Event()
    first_calls = []
    second_calls = []

    async def first_handler(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        first_calls.append(job.id)
        assert first_calls == ["a-first"], "a retried worker must not take another job or repeat completed work"
        await write_memory(conn, job, tick)
        first_entered.set()
        await raise_transient.wait()
        await conn.execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic retry' USING ERRCODE='40001'; END $$")

    async def second_handler(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        second_calls.append(job.id)
        await write_memory(conn, job, tick)
        second_entered.set()
        await finish_second.wait()

    async with connect(database) as first, connect(database) as second, connect(database) as observer:
        await identity(first, "u1")
        await identity(second, "u1")
        first_pid = await scalar(first, "SELECT pg_backend_pid()")
        second_pid = await scalar(second, "SELECT pg_backend_pid()")
        tasks = []
        try:
            tasks.append(
                asyncio.create_task(process_one(first, "u1", first_handler, schema_name=schema, replay_time=BASE))
            )
            await asyncio.wait_for(first_entered.wait(), timeout=5)
            tasks.append(
                asyncio.create_task(process_one(second, "u1", second_handler, schema_name=schema, replay_time=BASE))
            )
            await wait_for_advisory_wait(observer, second_pid)
            # Worker two is already queued on the advisory lock before worker
            # one rolls back; it therefore claims the original queued job.
            raise_transient.set()
            await asyncio.wait_for(second_entered.wait(), timeout=5)
            await wait_for_advisory_wait(observer, first_pid)
            finish_second.set()
            first_result, second_result = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
            assert first_result.status == second_result.status == "done"
            assert first_result.job_id == second_result.job_id == "a-first"
            assert first_calls == second_calls == ["a-first"]
            assert await scalar(observer, "SELECT status FROM job WHERE id='b-second'") == "queued"
            assert await scalar(observer, "SELECT count(*) FROM memory_version") == 1
        finally:
            raise_transient.set()
            finish_second.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_exhausted_transient_job_is_dead_letter_without_partial_memory_and_other_user_continues(
    database,
) -> None:
    _, schema = database
    await submit(database, new_job("exhausted"))
    await submit(database, new_job("survivor", "u2"))
    calls = []

    async def always_fails(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        calls.append(job.id)
        await write_memory(conn, job, tick)
        await conn.execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic deadlock' USING ERRCODE='40P01'; END $$")

    async with connect(database) as conn:
        await identity(conn, "u1")
        result = await process_one(conn, "u1", always_fails, schema_name=schema, replay_time=BASE)
        assert result.status == "dead_letter" and result.attempts == result.transaction_attempts == 3
        assert calls == ["exhausted"] * 3
        assert await scalar(conn, "SELECT count(*) FROM memory_version WHERE user_id='u1'") == 0
        assert await scalar(conn, "SELECT count(*) FROM audit_event WHERE user_id='u1'") == 0
        assert await scalar(conn, "SELECT status FROM job WHERE id='exhausted'") == "dead_letter"
        await identity(conn, "u2")
        assert (await process_one(conn, "u2", write_memory, schema_name=schema, replay_time=BASE)).status == "done"


async def test_real_exclusion_conflict_is_not_blindly_retried(database) -> None:
    _, schema = database
    await submit(database, new_job("semantic-conflict"))
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO memory_version (
                       id,memory_key,user_id,status,subject_key,predicate_key,single_valued,content,event_range,recorded_range
                   ) VALUES ('existing','state','u1','active','self','state',true,'old',
                             tstzrange(%s,NULL,'[)'),tstzrange(%s,NULL,'[)'))""",
                (BASE, BASE),
            )
            await conn.execute("INSERT INTO memory_evidence VALUES ('existing','turn-u1','u1','synthetic','support')")
        calls = []

        async def conflicting(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
            calls.append(job.id)
            await conn.execute(
                """INSERT INTO memory_version (
                       id,memory_key,user_id,status,subject_key,predicate_key,single_valued,content,event_range,recorded_range
                   ) VALUES ('conflicting','state','u1','active','self','state',true,'new',
                             tstzrange(%s,NULL,'[)'),tstzrange(clock_now(),NULL,'[)'))""",
                (BASE,),
            )

        result = await process_one(conn, "u1", conflicting, schema_name=schema, replay_time=BASE)
        assert result.status == "dead_letter" and result.attempts == result.transaction_attempts == 1
        assert calls == ["semantic-conflict"]
        assert await scalar(conn, "SELECT array_agg(id ORDER BY id) FROM memory_version") == ["existing"]


async def test_cancellation_rolls_back_job_memory_and_clock_then_allows_safe_retry(database) -> None:
    _, schema = database
    await submit(database, new_job("cancelled"))
    entered = asyncio.Event()
    never = asyncio.Event()

    async def interrupted(conn: AsyncConnection, job: Job, tick: ClockTick) -> None:
        await write_memory(conn, job, tick)
        entered.set()
        await never.wait()

    async with connect(database) as conn:
        await identity(conn, "u1")
        task = asyncio.create_task(process_one(conn, "u1", interrupted, schema_name=schema, replay_time=BASE))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert conn.info.transaction_status == pq.TransactionStatus.IDLE
            assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0
            assert await scalar(conn, "SELECT count(*) FROM audit_event") == 0
            assert await scalar(conn, "SELECT status FROM job WHERE id='cancelled'") == "queued"
            assert await scalar(conn, "SELECT attempts FROM job WHERE id='cancelled'") == 0
            assert (await process_one(conn, "u1", write_memory, schema_name=schema, replay_time=BASE)).status == "done"
            assert await scalar(conn, "SELECT count(*) FROM memory_version") == 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_queue_requires_matching_tenant_and_worker_owns_the_whole_transaction(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u2")
        with pytest.raises(InsufficientPrivilege):
            await enqueue(conn, new_job("wrong-user"), schema_name=schema)
        with pytest.raises(InsufficientPrivilege):
            await process_one(conn, "u1", write_memory, schema_name=schema, replay_time=BASE)
        await identity(conn, "u1")
        async with conn.transaction():
            with pytest.raises(QueueError):
                await process_one(conn, "u1", write_memory, schema_name=schema, replay_time=BASE)
        assert await scalar(conn, "SELECT count(*) FROM job") == 0


async def test_clock_and_queue_work_under_actual_non_superuser_rls(database) -> None:
    _, schema = database
    role = "cme_clockqueue_role_" + uuid4().hex
    await submit(database, new_job("private-u1"))
    await submit(database, new_job("private-u2", "u2"))
    async with connect(database) as conn:
        await conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
        try:
            await conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role))
            )
            await conn.execute(
                sql.SQL("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            await conn.execute(
                sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            await conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            assert (
                await scalar(conn, "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user") is False
            )
            await identity(conn, "u1")
            assert await scalar(conn, "SELECT array_agg(id ORDER BY id) FROM job") == ["private-u1"]
            result = await process_one(conn, "u1", write_memory, schema_name=schema, replay_time=BASE)
            assert result.status == "done" and result.job_id == "private-u1"
            with pytest.raises(InsufficientPrivilege):
                await process_one(conn, "u2", write_memory, schema_name=schema, replay_time=BASE)
            assert await scalar(conn, "SELECT array_agg(user_id) FROM memory_version") == ["u1"]
            assert await scalar(conn, "SELECT count(*) FROM audit_event WHERE user_id='u2'") == 0
            await identity(conn, "u2")
            # SUPERSEDED v1 behaviour (V2-D49 D12, migration 11): the idempotency key is unique PER USER, so another
            # tenant's key is no longer a collision. It used to raise JobIdentityConflict, which was a cross-tenant
            # existence oracle for the key (THREAT_MODEL T-13). u2 now simply owns a job with the same key, and still
            # sees nothing of u1's.
            colliding = await enqueue(
                conn, new_job("same-key-other-tenant", "u2", idempotency_key="key-private-u1"), schema_name=schema
            )
            assert colliding.created is True and colliding.job.user_id == "u2"
            assert await scalar(conn, "SELECT array_agg(id ORDER BY id) FROM job") == [
                "private-u2",
                "same-key-other-tenant",
            ]
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            await conn.execute(
                sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            await conn.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(sql.Identifier(schema), sql.Identifier(role))
            )
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
