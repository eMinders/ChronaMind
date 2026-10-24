"""RL-012/RL-013 probes on disposable schemas, not production DDL or an RL-093 load test."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest
import pytest_asyncio
from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection, errors, sql
from psycopg.types.range import Range
from psycopg_pool import AsyncConnectionPool

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig

pytestmark = pytest.mark.integration
T = datetime(2026, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist installed")
    config = DatabaseConfig.from_environment()
    schema = "cme_probe_" + uuid4().hex
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield config, schema
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET statement_timeout = '5s'")
        yield conn


async def test_versions_range_vector_and_hnsw(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as conn:
        cursor = await conn.execute(
            "SELECT current_setting('server_version_num')::int, "
            "(SELECT extversion FROM pg_extension WHERE extname='vector'), "
            "(SELECT extversion FROM pg_extension WHERE extname='btree_gist')"
        )
        assert await cursor.fetchone() == (160015, "0.8.7", "1.7")
        await register_vector_async(conn)
        await conn.execute(
            "CREATE TABLE values_probe (id int PRIMARY KEY, tenant text, period tstzrange, embedding vector(384))"
        )
        embedding = np.zeros(384, dtype=np.float32)
        embedding[0] = 1
        period = Range(T, None, bounds="[)")
        await conn.execute("INSERT INTO values_probe VALUES (%s, %s, %s, %s)", (1, "a", period, embedding))
        cursor = await conn.execute("SELECT period, embedding FROM values_probe WHERE id=1")
        row = await cursor.fetchone()
        assert row is not None and row[0] == period
        assert np.array_equal(row[1], embedding)
        await conn.execute("CREATE INDEX probe_hnsw ON values_probe USING hnsw (embedding vector_cosine_ops)")
        async with conn.transaction():
            await conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            cursor = await conn.execute(
                "SELECT id FROM values_probe WHERE tenant=%s ORDER BY embedding <=> %s LIMIT 1", ("a", embedding)
            )
            assert await cursor.fetchone() == (1,)
        # This proves adapter/index/GUC compatibility, not approximate recall at scale.


async def test_pool_transaction_local_clock_cleanup(database: tuple[DatabaseConfig, str]) -> None:
    config, _ = database
    async with AsyncConnectionPool(
        config.database_url.get_secret_value(),
        min_size=1,
        max_size=1,
        open=False,
        kwargs={"autocommit": True, "connect_timeout": 5},
    ) as pool:
        await pool.wait(timeout=10)
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.clock', %s, true)", (T.isoformat(),))
                cursor = await conn.execute("SELECT current_setting('app.clock')::timestamptz")
                assert await cursor.fetchone() == (T,)
        async with pool.connection() as conn:
            cursor = await conn.execute("SELECT nullif(current_setting('app.clock', true), '')")
            assert await cursor.fetchone() == (None,)
            with pytest.raises(RuntimeError, match="rollback_probe"):
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.clock', %s, true)", (T.isoformat(),))
                    raise RuntimeError("rollback_probe")
        async with pool.connection() as conn:
            cursor = await conn.execute("SELECT nullif(current_setting('app.clock', true), '')")
            assert await cursor.fetchone() == (None,)


async def test_exclusion_and_close_then_insert(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as conn:
        await conn.execute("""CREATE TABLE belief (
            id int PRIMARY KEY, user_id text NOT NULL, subject_key text NOT NULL,
            predicate_key text NOT NULL, valid_range tstzrange NOT NULL,
            recorded_range tstzrange NOT NULL, single_valued bool NOT NULL, status text NOT NULL,
            EXCLUDE USING gist (user_id WITH =, subject_key WITH =, predicate_key WITH =,
                                valid_range WITH &&, recorded_range WITH &&)
            WHERE (single_valued AND status IN ('active', 'archived'))
        )""")
        period = Range(T, None, bounds="[)")
        await conn.execute(
            "INSERT INTO belief VALUES (1, 'a', 'user', 'diet', %s, %s, true, 'active')", (period, period)
        )
        with pytest.raises(errors.ExclusionViolation):
            await conn.execute(
                "INSERT INTO belief VALUES (2, 'a', 'user', 'diet', %s, %s, true, 'active')", (period, period)
            )
        later = T + timedelta(days=1)
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("a",))
            await conn.execute(
                "UPDATE belief SET recorded_range=tstzrange(lower(recorded_range), %s, '[)') WHERE id=1", (later,)
            )
            await conn.execute(
                "INSERT INTO belief VALUES (2, 'a', 'user', 'diet', %s, %s, true, 'active')",
                (period, Range(later, None, bounds="[)")),
            )
        cursor = await conn.execute("SELECT id FROM belief WHERE recorded_range @> %s", (later,))
        assert await cursor.fetchall() == [(2,)]
        await conn.execute(
            "INSERT INTO belief VALUES (3, 'a', 'user', 'diet', %s, %s, false, 'active')", (period, period)
        )


async def test_transaction_advisory_lock_and_skip_locked(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as first, connect(database) as second:
        await first.execute("CREATE TABLE job_probe (id int PRIMARY KEY)")
        await first.execute("INSERT INTO job_probe VALUES (1), (2)")
        async with first.transaction():
            await first.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("same-user",))
            await first.execute("SELECT id FROM job_probe ORDER BY id LIMIT 1 FOR UPDATE")
            async with second.transaction():
                cursor = await second.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", ("same-user",))
                assert await cursor.fetchone() == (False,)
                cursor = await second.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", ("other-user",))
                assert await cursor.fetchone() == (True,)
                cursor = await second.execute("SELECT id FROM job_probe ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")
                assert await cursor.fetchone() == (2,)
        async with second.transaction():
            cursor = await second.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", ("same-user",))
            assert await cursor.fetchone() == (True,)


async def test_two_writer_exclusion_race(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as first, connect(database) as second:
        await first.execute(
            "CREATE TABLE race_probe (id int, user_id text, period tstzrange, "
            "EXCLUDE USING gist (user_id WITH =, period WITH &&))"
        )

        async def competing_insert() -> None:
            await second.execute("INSERT INTO race_probe VALUES (2, 'a', tstzrange(%s, NULL))", (T,))

        task: asyncio.Task[None] | None = None
        try:
            async with first.transaction():
                await first.execute("INSERT INTO race_probe VALUES (1, 'a', tstzrange(%s, NULL))", (T,))
                task = asyncio.create_task(competing_insert())
                # Wait for actual server-side contention, not merely task creation.
                async with asyncio.timeout(3):
                    while True:
                        cursor = await first.execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (second.info.backend_pid,)
                        )
                        if await cursor.fetchone() == ("Lock",):
                            break
                        if task.done():
                            await task
                            pytest.fail("competing_insert_did_not_wait_for_lock")
                        await asyncio.sleep(0.01)
            with pytest.raises(errors.ExclusionViolation):
                await asyncio.wait_for(task, timeout=10)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        cursor = await first.execute("SELECT count(*) FROM race_probe")
        assert await cursor.fetchone() == (1,)
