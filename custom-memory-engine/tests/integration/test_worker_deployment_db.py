"""The worker's deployment contract, tested against a real queue (`V2-D103`, `V2-D106`).

Deployment behaviour is code, so it is tested like code: lease recovery, dead-lettering after the attempt budget, the
health probe's verdict under the restricted role, and the maintenance branch. The process itself — a real
`scripts/run_worker.py` child, SIGTERM, a crash with a held lease and the restart that recovers it — is driven in
`test_worker_process_db.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg import AsyncConnection, sql

from custom_memory_engine.queue import NewJob, enqueue
from custom_memory_engine.semantic.slow_lane import LEASE, claim_one, complete_one, recover_stale
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)
from tests.integration.test_slow_lane_db import world  # noqa: F811  (fixture: alice, a role, a schema)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, tzinfo=UTC)


async def job_for(conn: AsyncConnection, *, schema: str, user: str = "alice", key: str = "k1") -> Any:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        created = await enqueue(
            conn,
            NewJob(id=f"job-{key}", user_id=user, kind="semantic_extraction", idempotency_key=key, turn_id="t-alice"),
            schema_name=schema,
        )
    return created.job


async def test_a_lease_past_its_window_is_returned_to_the_queue(database) -> None:  # noqa: F811
    """The one state the two-transaction lane adds: a worker that died holding a lease loses the lease, not the job."""
    async with world(database) as (conn, schema):
        await job_for(conn, schema=schema)
        claimed = await claim_one(conn, "alice", schema_name=schema)
        assert claimed is not None
        # the lease is `next_run_at`; move it into the past to simulate a worker that died (no clock rewinding needed)
        await conn.execute(
            sql.SQL("UPDATE {t} SET next_run_at = pg_catalog.transaction_timestamp() - %s WHERE id = %s").format(
                t=sql.Identifier(schema, "job")
            ),
            (LEASE + timedelta(seconds=1), claimed.id),
        )
        recovered = await recover_stale(conn, "alice", schema_name=schema)
        assert recovered == 1
        row = await conn.execute(
            sql.SQL("SELECT status FROM {t} WHERE id = %s").format(t=sql.Identifier(schema, "job")), (claimed.id,)
        )
        assert await row.fetchone() == ("queued",)


async def test_the_probe_reports_healthy_degraded_and_unhealthy(database) -> None:  # noqa: F811
    """`scripts/worker_healthcheck.py`: exit 0 / 1 / 2 from the queue's own state — **under the restricted role**.

    The probe is deployment code, but its verdict is what an operator pages on, so it is asserted rather than assumed:
    an empty queue is healthy, a backlog older than the window is degraded, and a lease expired past its grace is
    unhealthy. It runs as the provisioned runtime role because that is where the first version was wrong: `job` forces
    row-level security, and the probe asked without `app.user_id`. The test DSN is a superuser, which bypasses RLS,
    so the bug was invisible here and would have reported "healthy" over any backlog in a real deployment.
    """
    from custom_memory_engine.queue.health import queue_health
    from custom_memory_engine.security import provision_runtime_role

    async with world(database) as (conn, schema):
        config, _ = database
        dsn = config.database_url.get_secret_value()
        role = "cme_rt_probe_" + schema[-8:]
        await provision_runtime_role(conn, schema, role)
        await conn.commit()

        async def verdict() -> str:
            async with await AsyncConnection.connect(dsn, autocommit=True) as probe:
                return (await queue_health(probe, users=("alice",), schema_name=schema, role=role)).verdict

        assert await verdict() == "healthy"
        await job_for(conn, schema=schema)
        # due now and therefore within the window: still healthy
        assert await verdict() == "healthy"
        # a backlog older than the alert window
        await conn.execute(
            sql.SQL("UPDATE {t} SET next_run_at = pg_catalog.transaction_timestamp() - interval '2 hours'").format(
                t=sql.Identifier(schema, "job")
            )
        )
        assert await verdict() == "degraded"
        # the control that makes this test meaningful: under the role, an *unbound* read of the queue sees nothing, so
        # a probe that forgot `app.user_id` would answer "healthy" to the backlog asserted above
        async with await AsyncConnection.connect(dsn, autocommit=True) as unbound, unbound.transaction():
            await unbound.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            cursor = await unbound.execute(sql.SQL("SELECT count(*) FROM {t}").format(t=sql.Identifier(schema, "job")))
            assert await cursor.fetchone() == (0,)
        # a lease that expired an hour ago and was never returned: no loop is running, so unhealthy
        await conn.execute(
            sql.SQL(
                "UPDATE {t} SET status = 'running', next_run_at = pg_catalog.transaction_timestamp()"
                " - interval '1 hour'"
            ).format(t=sql.Identifier(schema, "job"))  # fmt: skip
        )
        assert await verdict() == "unhealthy"


async def test_a_job_that_fails_repeatedly_is_dead_lettered_rather_than_retried_forever(database) -> None:  # noqa: F811
    """`MAX_ATTEMPTS` then `dead_letter`: a poison job must become visible, not immortal."""
    from custom_memory_engine.queue.worker import MAX_ATTEMPTS

    async with world(database) as (conn, schema):
        await job_for(conn, schema=schema)

        async def boom(*_: object) -> None:
            raise RuntimeError("model_down")

        statuses = []
        for _ in range(MAX_ATTEMPTS + 1):
            claimed = await claim_one(conn, "alice", schema_name=schema)
            if claimed is None:
                # the failure path sets a backoff, so the job is not due again until it elapses; move it back to now
                await conn.execute(
                    sql.SQL("UPDATE {t} SET next_run_at = pg_catalog.transaction_timestamp()").format(
                        t=sql.Identifier(schema, "job")
                    )
                )
                claimed = await claim_one(conn, "alice", schema_name=schema)
            assert claimed is not None
            result = await complete_one(conn, "alice", claimed, boom, schema_name=schema)
            statuses.append(result.status)
            if result.status == "dead_letter":
                break
        assert "dead_letter" in statuses
        job_table = sql.Identifier(schema, "job")
        cursor = await conn.execute(sql.SQL("SELECT status, attempts FROM {t}").format(t=job_table))
        status, attempts = await cursor.fetchone()  # type: ignore[misc]
        assert status == "dead_letter" and attempts == MAX_ATTEMPTS


async def test_a_queued_index_rebuild_is_worked_rather_than_left_in_the_queue(database) -> None:  # noqa: F811
    """`rebuild_index` answers with a job id, so something has to run it (`A-014`, `V2-D103`).

    The gap this closes was real: the operation enqueued `index_rebuild` and the worker claimed only
    `semantic_extraction`, so every maintenance job sat `queued` for ever and looked like a stalled worker. The test
    drives the `closure_hash` branch, which runs inside the job's transaction, and asserts the *other* branch is
    refused there: `REINDEX ... CONCURRENTLY` needs an idle autocommit connection, and a handler that ran it inline
    would block every read of the corpus behind an exclusive lock.
    """
    from custom_memory_engine.engine.index_maintenance import (
        MaintenanceError,
        perform_inline,
        reindex_concurrently,
    )

    async with world(database) as (conn, schema):
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            created = await enqueue(
                conn,
                NewJob(
                    id="job-rebuild",
                    user_id="alice",
                    kind="index_rebuild",
                    idempotency_key="rebuild-1",
                    payload={"index": "closure_hash"},
                ),
                schema_name=schema,
            )
        assert created.job.kind == "index_rebuild"
        claimed = await claim_one(conn, "alice", schema_name=schema, kinds=("semantic_extraction", "index_rebuild"))
        assert claimed is not None and claimed.kind == "index_rebuild"

        async def reseal(conn_: AsyncConnection, job: object, tick: object) -> None:
            del tick
            await perform_inline(conn_, job, schema_name=schema)  # type: ignore[arg-type]

        result = await complete_one(conn, "alice", claimed, reseal, schema_name=schema)
        assert result.status == "done"
        cursor = await conn.execute(
            sql.SQL("SELECT status FROM {t} WHERE id = 'job-rebuild'").format(t=sql.Identifier(schema, "job"))
        )
        assert await cursor.fetchone() == ("done",)
        # the two shapes are not interchangeable, and the refusal says which one this call needed
        with pytest.raises(MaintenanceError) as refused:
            await perform_inline(conn, claimed.model_copy(update={"payload": {"index": "fts"}}), schema_name=schema)
        assert refused.value.code == "index_requires_its_own_connection"
        # …and the concurrent form refuses a connection that is inside a transaction, rather than locking the corpus
        async with conn.transaction():
            with pytest.raises(MaintenanceError) as inside:
                await reindex_concurrently(conn, "fts", schema_name=schema)
            assert inside.value.code == "maintenance_requires_idle_connection"
