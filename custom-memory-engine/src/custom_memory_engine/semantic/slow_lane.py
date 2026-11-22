"""Two-transaction job execution for work that leaves the database (``E-047``).

``process_one`` deliberately runs a handler inside **one** transaction: it is built for database-only work, and says
so. Model extraction is not database-only — it is a network call that takes seconds. Running it inside that
transaction would hold row locks and a pool connection for the duration, and a serialization retry would re-bill the
model, which is both slow and expensive.

So this module adds the other shape, and only the other shape:

1. :func:`claim_one` — **transaction A**: take the head of the queue with ``FOR UPDATE SKIP LOCKED``, mark it
   ``running``, count the attempt, commit. The lock is gone the moment it returns.
2. the caller does the slow thing (the model) with no transaction open;
3. :func:`complete_one` — **transaction B**: write the result and mark the job ``done``, or record the failure with
   backoff and move it to ``dead_letter`` after ``MAX_ATTEMPTS``.

The cost of splitting is a state the single-transaction version never has: a worker that dies between (1) and (3)
leaves a job ``running`` for ever. :func:`recover_stale` closes that hole by returning any job whose lease expired to
the queue, which is the same trade every durable queue makes and the reason the ``running`` status exists at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

import structlog
from psycopg import AsyncConnection, pq, sql
from psycopg.rows import dict_row

from custom_memory_engine.clock import ClockTick, allocate_clock
from custom_memory_engine.queue.contracts import Job, NewJob, QueueError, WorkerResult
from custom_memory_engine.queue.worker import JOB_COLUMNS, MAX_ATTEMPTS, _schema, _user

logger = structlog.get_logger(__name__)

#: How long a claimed job may sit in ``running`` before another worker may take it back. Long enough for one slow-lane
#: extraction with a retrying model call, short enough that a crashed worker's job is not lost for a shift.
LEASE = timedelta(minutes=10)

#: The work that runs inside transaction B: it writes the extraction and nothing else. It must not call a model.
type ResultWriter = Callable[[AsyncConnection, Job, ClockTick], Awaitable[None]]


async def claim_one(
    connection: AsyncConnection,
    user_id: str,
    *,
    schema_name: str = "cme_engine",
    kinds: Sequence[str] | None = None,
) -> Job | None:
    """Take one queued job, or ``None``. Returns with the job ``running`` and its attempt counted."""
    _user(user_id)
    table = _schema(schema_name)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise QueueError("claim_requires_idle_connection")
    async with connection.transaction():
        await connection.execute("SELECT pg_catalog.set_config('app.user_id', %s, true)", (user_id,))
        kind_filter = ""
        params: list[object] = [user_id]
        if kinds:
            if not all(isinstance(kind, str) and kind.strip() and "\x00" not in kind for kind in kinds):
                raise QueueError("job_kind_invalid")
            kind_filter = " AND kind = ANY(%s)"
            params.append(list(kinds))
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT " + JOB_COLUMNS + " FROM {} WHERE user_id=%s AND status='queued' "
                    "AND next_run_at<=pg_catalog.transaction_timestamp()" + kind_filter + " "
                    "ORDER BY next_run_at,id FOR UPDATE SKIP LOCKED LIMIT 1"
                ).format(table),
                tuple(params),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            job = Job.model_validate(row)
            await cursor.execute(
                sql.SQL(
                    "UPDATE {} SET status='running', attempts=attempts+1, "
                    "next_run_at=pg_catalog.transaction_timestamp() + %s WHERE id=%s AND user_id=%s"
                ).format(table),
                (LEASE, job.id, user_id),
            )
    logger.info("slow_lane_job_claimed", user_id=user_id, kind=job.kind, attempts=job.attempts + 1)
    return job.model_copy(update={"status": "running", "attempts": job.attempts + 1})


async def complete_one(
    connection: AsyncConnection,
    user_id: str,
    job: Job,
    work: ResultWriter,
    *,
    schema_name: str = "cme_engine",
) -> WorkerResult:
    """Run ``work`` and mark the job done — or record the failure with backoff / dead-letter it.

    The failure path is the reason this is not a bare ``try/finally``: a job that fails must keep its attempt count,
    wait exponentially, and eventually stop, or the queue becomes an infinite retry loop against a broken endpoint.
    """
    _user(user_id)
    table = _schema(schema_name)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise QueueError("completion_requires_idle_connection")
    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_catalog.set_config('app.user_id', %s, true)", (user_id,))
            tick = await allocate_clock(connection, user_id, schema_name=schema_name)
            await work(connection, job, tick)
            await connection.execute(
                # `payload` is cleared: a finished job must not keep a copy of anything derived from the turn, because
                # `job` rows outlive the memory they produced and are outside the deletion boundary's reach.
                sql.SQL(
                    "UPDATE {} SET status='done', payload=NULL WHERE id=%s AND user_id=%s AND status='running'"
                ).format(table),
                (job.id, user_id),
            )
    except Exception as error:  # noqa: BLE001 - a failed job is data, not a crash
        if connection.closed or connection.info.transaction_status != pq.TransactionStatus.IDLE:
            raise QueueError("completion_failure_unavailable") from None
        return await _record_failure(connection, user_id, table, job, error)
    logger.info("slow_lane_job_done", user_id=user_id, kind=job.kind, attempts=job.attempts)
    return WorkerResult(status="done", job_id=job.id, attempts=job.attempts)


async def _record_failure(
    connection: AsyncConnection,
    user_id: str,
    table: sql.Identifier,
    job: Job,
    error: BaseException,
) -> WorkerResult:
    """Back off, or give up. Only the exception *type* is recorded: a message can carry the turn's text."""
    terminal = job.attempts >= MAX_ATTEMPTS
    status = "dead_letter" if terminal else "queued"
    delay = timedelta(0) if terminal else timedelta(seconds=min(300, 2**job.attempts))
    async with connection.transaction():
        await connection.execute("SELECT pg_catalog.set_config('app.user_id', %s, true)", (user_id,))
        # No `error_type` column exists and none is added: the failure reason is a log line (content-free) and the
        # attempt count is the durable fact. A migration to persist a reason would have to solve its own deletion
        # question, which is not worth it for a diagnostic.
        await connection.execute(
            sql.SQL(
                "UPDATE {} SET status=%s, next_run_at=pg_catalog.transaction_timestamp() + %s "
                "WHERE id=%s AND user_id=%s"
            ).format(table),
            (status, delay, job.id, user_id),
        )
    logger.warning(
        "slow_lane_job_failed",
        user_id=user_id,
        kind=job.kind,
        attempts=job.attempts,
        terminal=terminal,
        error_type=type(error).__name__,
        # a static code (our own errors carry one); never a message or an argument
        error_code=str(error)[:64] if type(error).__name__.endswith(("Error", "Violation")) else None,
    )
    return WorkerResult(
        status="dead_letter" if terminal else "idle",
        job_id=job.id,
        attempts=job.attempts,
        reason="job_failed" if not terminal else "attempts_exhausted",
    )


async def recover_stale(
    connection: AsyncConnection,
    user_id: str,
    *,
    schema_name: str = "cme_engine",
) -> int:
    """Return jobs whose lease expired to the queue and report how many. Called at worker start-up."""
    _user(user_id)
    table = _schema(schema_name)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise QueueError("recovery_requires_idle_connection")
    async with connection.transaction():
        await connection.execute("SELECT pg_catalog.set_config('app.user_id', %s, true)", (user_id,))
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "UPDATE {} SET status='queued', next_run_at=pg_catalog.transaction_timestamp() "
                    "WHERE user_id=%s AND status='running' AND next_run_at <= pg_catalog.transaction_timestamp()"
                ).format(table),
                (user_id,),
            )
            recovered = cursor.rowcount or 0
    if recovered:
        logger.warning("slow_lane_jobs_recovered", user_id=user_id, recovered=recovered)
    return int(recovered)


def enqueue_slow_lane(
    *,
    job_id: str,
    user_id: str,
    turn_id: str,
    extractor_version: str,
    candidate_fingerprint: str,
    not_before: datetime | None = None,
) -> NewJob:
    """The ``NewJob`` for one turn's extraction, with the identity the queue's uniqueness is built on.

    ``(turn_id, extractor_version, candidate_fingerprint)`` is the queue's second unique identity, so re-enqueueing the
    same turn for the same extractor version is a no-op and a *different* extractor version is a new job. That is what
    makes a re-extraction rollout expressible without duplicating work.
    """
    return NewJob(
        id=job_id,
        user_id=user_id,
        kind="semantic_extraction",
        idempotency_key=f"extract:{turn_id}:{extractor_version}:{candidate_fingerprint}",
        turn_id=turn_id,
        extractor_version=extractor_version,
        candidate_fingerprint=candidate_fingerprint,
        not_before=not_before.astimezone(UTC) if not_before is not None else None,
    )


__all__ = ["LEASE", "ResultWriter", "claim_one", "complete_one", "enqueue_slow_lane", "recover_stale"]
