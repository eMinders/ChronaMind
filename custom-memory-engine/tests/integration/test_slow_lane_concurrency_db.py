"""Concurrency, SKIP LOCKED, and zombie lease-fencing tests for ``semantic/slow_lane.py`` (``#252``)."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.deletion.closure import StaleDeletionEpoch
from custom_memory_engine.queue.worker import MAX_ATTEMPTS, enqueue
from custom_memory_engine.security import provision_runtime_role
from custom_memory_engine.semantic.slow_lane import claim_one, complete_one, enqueue_slow_lane, recover_stale
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)


@dataclass
class ConcurrencyRig:
    owner: AsyncConnection
    conn1: AsyncConnection
    conn2: AsyncConnection
    schema: str
    role: str

    async def enqueue_job(self, job_id: str, turn_id: str = "t-1", *, version: str = "v1") -> None:
        await self.owner.execute("SELECT set_config('app.user_id', 'alice', false)")
        await enqueue(
            self.owner,
            enqueue_slow_lane(
                job_id=job_id,
                user_id="alice",
                turn_id=turn_id,
                extractor_version=version,
                candidate_fingerprint="f" * 32,
            ),
            schema_name=self.schema,
        )
        await self.owner.commit()

    async def job_state(self, job_id: str) -> tuple[str, int, object] | None:
        cursor = await self.owner.execute(
            sql.SQL("SELECT status, attempts, payload FROM {} WHERE id = %s").format(
                sql.Identifier(self.schema, "job")
            ),
            (job_id,),
        )
        row = await cursor.fetchone()
        await self.owner.commit()
        if row is None:
            return None
        return (str(row[0]), int(row[1]), row[2])


@asynccontextmanager
async def rig(database) -> AsyncIterator[ConcurrencyRig]:  # noqa: F811
    config, schema = database
    role = "cme_rt_slc_" + uuid4().hex[:10]
    dsn = config.database_url.get_secret_value()
    async with connect(database) as owner:
        await owner.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (T0,))
        await owner.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', %s)", (T0,))
        for idx in (1, 2):
            await owner.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES (%s, 's-alice', 'alice', %s, 'user', %s, %s)",
                (f"t-{idx}", idx, f"Turn content {idx}", T0),
            )
        await provision_runtime_role(owner, schema, role)
        await owner.commit()
        async with (
            await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=5) as conn1,
            await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=5) as conn2,
        ):
            for c in (conn1, conn2):
                await c.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
                await c.execute("SET timezone = 'UTC'")
            try:
                yield ConcurrencyRig(owner, conn1, conn2, schema, role)
            finally:
                await owner.rollback()
                await owner.execute("RESET ROLE")
                await owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await owner.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
                await owner.commit()


async def test_two_concurrent_claim_one_calls_take_distinct_jobs(database) -> None:  # noqa: F811
    async with rig(database) as r:
        await r.enqueue_job("j-1", "t-1", version="v1")
        await r.enqueue_job("j-2", "t-2", version="v1")

        first, second = await asyncio.gather(
            claim_one(r.conn1, "alice", schema_name=r.schema, role=r.role),
            claim_one(r.conn2, "alice", schema_name=r.schema, role=r.role),
        )
        assert first is not None and second is not None
        assert {first.id, second.id} == {"j-1", "j-2"}
        assert first.status == second.status == "running"
        assert first.attempts == second.attempts == 1


async def test_single_job_with_two_concurrent_claims_yields_one_job_and_one_none(database) -> None:  # noqa: F811
    async with rig(database) as r:
        await r.enqueue_job("j-only", "t-1", version="v1")

        first, second = await asyncio.gather(
            claim_one(r.conn1, "alice", schema_name=r.schema, role=r.role),
            claim_one(r.conn2, "alice", schema_name=r.schema, role=r.role),
        )
        claimed = [item for item in (first, second) if item is not None]
        assert len(claimed) == 1
        assert claimed[0].id == "j-only"
        assert claimed[0].attempts == 1


async def test_skip_locked_is_present_and_load_bearing_in_claim_and_recovery(database) -> None:  # noqa: F811
    """Control test: holding a FOR UPDATE lock on the head job lets a second worker claim the next job immediately."""
    assert "FOR UPDATE SKIP LOCKED" in inspect.getsource(claim_one)
    assert "FOR UPDATE SKIP LOCKED" in inspect.getsource(recover_stale)

    async with rig(database) as r:
        await r.enqueue_job("j-1", "t-1", version="v1")
        await r.enqueue_job("j-2", "t-2", version="v1")
        # Order j-1 strictly before j-2.
        await r.owner.execute(
            sql.SQL("UPDATE {} SET next_run_at = pg_catalog.now() - interval '10 seconds' WHERE id = 'j-1'").format(
                sql.Identifier(r.schema, "job")
            )
        )
        await r.owner.commit()

        # Hold a row lock on j-1 on conn1 inside an open transaction.
        await r.conn2.execute("SET statement_timeout = '1s'")
        async with r.conn1.transaction():
            await r.conn1.execute(
                sql.SQL("SELECT id FROM {} WHERE id = 'j-1' FOR UPDATE").format(sql.Identifier(r.schema, "job"))
            )
            # Without SKIP LOCKED, conn2 would block behind conn1 and hit statement_timeout.
            claimed = await claim_one(r.conn2, "alice", schema_name=r.schema, role=r.role)
            assert claimed is not None and claimed.id == "j-2"

        # Now test recover_stale skips a FOR UPDATE locked stale running row while recovering another stale row.
        await r.owner.execute(
            sql.SQL(
                "UPDATE {} SET status = 'running', attempts = 1, next_run_at = pg_catalog.now() - interval '1 hour'"
            ).format(sql.Identifier(r.schema, "job"))
        )
        await r.owner.commit()
        async with r.conn1.transaction():
            await r.conn1.execute(
                sql.SQL("SELECT id FROM {} WHERE id = 'j-1' FOR UPDATE").format(sql.Identifier(r.schema, "job"))
            )
            recovered = await recover_stale(r.conn2, "alice", schema_name=r.schema, role=r.role)
            assert recovered == 1
        assert (await r.job_state("j-1"))[:2] == ("running", 1)  # type: ignore[index]
        assert (await r.job_state("j-2"))[:2] == ("queued", 1)  # type: ignore[index]


async def test_zombie_worker_completion_is_fenced_after_lease_reclaim(database) -> None:  # noqa: F811
    async with rig(database) as r:
        await r.enqueue_job("j-fence", "t-1", version="v1")
        zombie_job = await claim_one(r.conn1, "alice", schema_name=r.schema, role=r.role)
        assert zombie_job is not None and zombie_job.attempts == 1

        # Expire Worker 1's lease and let Worker 2 recover + reclaim it.
        await r.owner.execute(
            sql.SQL("UPDATE {} SET next_run_at = pg_catalog.now() - interval '1 hour' WHERE id = 'j-fence'").format(
                sql.Identifier(r.schema, "job")
            )
        )
        await r.owner.commit()
        assert await recover_stale(r.conn2, "alice", schema_name=r.schema, role=r.role) == 1
        active_job = await claim_one(r.conn2, "alice", schema_name=r.schema, role=r.role)
        assert active_job is not None and active_job.attempts == 2

        work_called = False

        async def zombie_work(conn: AsyncConnection, _job: object, _tick: object) -> None:
            nonlocal work_called
            work_called = True
            await conn.execute(
                sql.SQL(
                    "INSERT INTO {} (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
                    "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, "
                    "visibility) VALUES ('v-zombie', 'k-zombie', 'alice', 'active', 'asserted', 'claim', 'self', "
                    "'statement', false, 'zombie write', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), "
                    "'semantic', '/u/alice/', 'private')"
                ).format(sql.Identifier(r.schema, "memory_version")),
                (T0,),
            )

        res_ok = await complete_one(r.conn1, "alice", zombie_job, zombie_work, schema_name=r.schema, role=r.role)
        assert res_ok.status == "idle" and res_ok.reason == "lease_lost"
        assert work_called is False
        assert (await r.job_state("j-fence"))[:2] == ("running", 2)  # type: ignore[index]

        # Even if the zombie path attempts to record a failure or stale epoch, fencing refuses to overwrite attempts=2.
        async def zombie_fail(*_: object) -> None:
            raise RuntimeError("zombie_model_timeout")

        async def zombie_stale(*_: object) -> None:
            raise StaleDeletionEpoch(0, 1)

        for fn in (zombie_fail, zombie_stale):
            res_err = await complete_one(r.conn1, "alice", zombie_job, fn, schema_name=r.schema, role=r.role)
            assert res_err.status == "idle" and res_err.reason == "lease_lost"
            assert (await r.job_state("j-fence"))[:2] == ("running", 2)  # type: ignore[index]

        # Zero claims written by the zombie worker.
        cursor = await r.owner.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(r.schema, "memory_version"))
        )
        assert await cursor.fetchone() == (0,)


async def test_recover_stale_dead_letters_jobs_at_max_attempts(database) -> None:  # noqa: F811
    async with rig(database) as r:
        await r.enqueue_job("j-dead", "t-1", version="v1")
        await r.owner.execute(
            sql.SQL(
                "UPDATE {} SET status = 'running', attempts = %s, "
                "next_run_at = pg_catalog.now() - interval '1 hour', "
                'payload = \'{{"secret": "payload"}}\'::jsonb '  # pragma: allowlist secret
                "WHERE id = 'j-dead'"
            ).format(sql.Identifier(r.schema, "job")),
            (MAX_ATTEMPTS,),
        )
        await r.owner.commit()

        recovered = await recover_stale(r.conn1, "alice", schema_name=r.schema, role=r.role)
        assert recovered == 1
        async with r.owner.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT status, attempts, payload FROM {} WHERE id = 'j-dead'").format(
                    sql.Identifier(r.schema, "job")
                )
            )
            assert await cursor.fetchone() == ("dead_letter", MAX_ATTEMPTS, None)
