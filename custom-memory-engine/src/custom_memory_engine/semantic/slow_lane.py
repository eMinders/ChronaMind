"""Two-transaction job execution for work that leaves the database (``E-047``, ``#252``).

``process_one`` deliberately runs a handler inside **one** transaction: it is built for database-only work, and says
so. Model extraction is not database-only — it is a network call that takes seconds. Running it inside that
transaction would hold row locks and a pool connection for the duration, and a serialization retry would re-bill the
model, which is both slow and expensive.

So this module adds the other shape, and only the other shape:

1. :func:`claim_one` — **transaction A**: take the head of the queue with ``FOR UPDATE SKIP LOCKED``, mark it
   ``running``, count the attempt, commit. The lock is gone the moment it returns.
2. the caller does the slow thing (the model) with no transaction open;
3. :func:`complete_one` — **transaction B**: write the result and mark the job ``done`` (fenced on
   ``status='running' AND attempts=%s``), or record the failure with backoff and move it to ``dead_letter`` after
   ``MAX_ATTEMPTS``.

The cost of splitting is a state the single-transaction version never has: a worker that dies between (1) and (3)
leaves a job ``running`` for ever. :func:`recover_stale` closes that hole using ``FOR UPDATE SKIP LOCKED`` by
returning any job whose lease expired to the queue — or dead-lettering it once ``attempts >= MAX_ATTEMPTS``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import structlog
from psycopg import AsyncConnection, pq, sql
from psycopg.errors import DeadlockDetected, SerializationFailure
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from custom_memory_engine.clock import ClockTick, allocate_clock
from custom_memory_engine.deletion.closure import StaleDeletionEpoch
from custom_memory_engine.queue.contracts import Job, NewJob, QueueError, WorkerResult
from custom_memory_engine.queue.worker import JOB_COLUMNS, MAX_ATTEMPTS, _schema, _user
from custom_memory_engine.security.audit_guard import content_free_details
from custom_memory_engine.security.scoping import tenant_scope

logger = structlog.get_logger(__name__)

#: How long a claimed job may sit in ``running`` before another worker may take it back. Long enough for one slow-lane
#: extraction with a retrying model call, short enough that a crashed worker's job is not lost for a shift.
LEASE = timedelta(minutes=10)

#: Postgres SQLSTATE codes safe to retry once under contention (serialization failure, deadlock detected).
RETRYABLE_PG_SQLSTATES = frozenset({"40001", "40P01"})

#: The work that runs inside transaction B: it writes the extraction and nothing else. It must not call a model.
type ResultWriter = Callable[[AsyncConnection, Job, ClockTick], Awaitable[None]]


class _LeaseLost(Exception):
    """Control-flow signal raised inside Transaction B when the job's lease was already reclaimed or completed."""


def _is_retryable_pg_error(error: BaseException) -> bool:
    return (
        isinstance(error, SerializationFailure | DeadlockDetected)
        or getattr(error, "sqlstate", None) in RETRYABLE_PG_SQLSTATES
    )


def _contention_retry() -> AsyncRetrying:
    return AsyncRetrying(
        retry=retry_if_exception(_is_retryable_pg_error),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.01, min=0.01, max=0.05),
        reraise=True,
    )


@asynccontextmanager
async def _tenant(connection: AsyncConnection, user_id: str, role: str | None) -> AsyncIterator[None]:
    """One transaction bound to ``user_id``, under the restricted ``role`` when one is given (``V2-D106``).

    The worker connects with an owner-level DSN (it must, to ``REINDEX``), so without a role every slow-lane write ran
    as the owner and row-level security was a promise the caller had to keep. With one, each transaction is exactly the
    host's: ``SET LOCAL ROLE`` plus ``app.user_id`` (`security.scoping.tenant_scope`), and both end with it.
    """
    if role is None:
        async with connection.transaction():
            await connection.execute("SELECT pg_catalog.set_config('app.user_id', %s, true)", (user_id,))
            yield
        return
    async with tenant_scope(connection, user_id, role=role):
        yield


async def claim_one(
    connection: AsyncConnection,
    user_id: str,
    *,
    schema_name: str = "cme_engine",
    kinds: Sequence[str] | None = None,
    role: str | None = None,
) -> Job | None:
    """Take one queued job, or ``None``. Returns with the job ``running`` and its attempt counted."""
    _user(user_id)
    table = _schema(schema_name)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise QueueError("claim_requires_idle_connection")
    kind_filter = ""
    params: list[object] = [user_id]
    if kinds:
        if not all(isinstance(kind, str) and kind.strip() and "\x00" not in kind for kind in kinds):
            raise QueueError("job_kind_invalid")
        kind_filter = " AND kind = ANY(%s)"
        params.append(list(kinds))
    job: Job | None = None
    try:
        async for attempt in _contention_retry():
            with attempt:
                async with _tenant(connection, user_id, role):
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
    except Exception as error:
        if _is_retryable_pg_error(error):
            raise QueueError("queue_contention_conflict") from None
        raise
    assert job is not None
    logger.info("slow_lane_job_claimed", user_id=user_id, kind=job.kind, attempts=job.attempts + 1)
    return job.model_copy(update={"status": "running", "attempts": job.attempts + 1})


async def complete_one(
    connection: AsyncConnection,
    user_id: str,
    job: Job,
    work: ResultWriter,
    *,
    schema_name: str = "cme_engine",
    role: str | None = None,
) -> WorkerResult:
    """Run ``work`` and mark the job done — or record the failure with backoff / dead-letter it.

    Every update on ``job`` is fenced by ``status='running' AND attempts=%s`` so a zombie worker whose lease expired
    and was reclaimed can never commit stale writes or overwrite a newer worker's state.
    """
    _user(user_id)
    table = _schema(schema_name)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise QueueError("completion_requires_idle_connection")
    try:
        async for attempt in _contention_retry():
            with attempt:
                async with _tenant(connection, user_id, role):
                    async with connection.cursor(row_factory=tuple_row) as cursor:
                        await cursor.execute(
                            sql.SQL("SELECT status, attempts FROM {} WHERE id=%s AND user_id=%s FOR UPDATE").format(
                                table
                            ),
                            (job.id, user_id),
                        )
                        state_row = await cursor.fetchone()
                    if state_row is None or (str(state_row[0]), int(state_row[1])) != ("running", job.attempts):
                        raise _LeaseLost
                    tick = await allocate_clock(connection, user_id, schema_name=schema_name)
                    await work(connection, job, tick)
                    async with connection.cursor(row_factory=tuple_row) as cursor:
                        await cursor.execute(
                            # `payload` is cleared: a finished job must not keep a copy of anything derived from the
                            # turn, because `job` rows outlive the memory they produced and are outside deletion's
                            # reach.
                            sql.SQL(
                                "UPDATE {} SET status='done', payload=NULL "
                                "WHERE id=%s AND user_id=%s AND status='running' AND attempts=%s "
                                "RETURNING id"
                            ).format(table),
                            (job.id, user_id, job.attempts),
                        )
                        if await cursor.fetchone() != (job.id,):
                            raise _LeaseLost
    except _LeaseLost:
        logger.warning("slow_lane_lease_lost", user_id=user_id, kind=job.kind, attempts=job.attempts)
        return WorkerResult(status="idle", job_id=job.id, attempts=job.attempts, reason="lease_lost")
    except Exception as error:  # noqa: BLE001 - a failed job is data, not a crash
        if _is_retryable_pg_error(error):
            raise QueueError("queue_contention_conflict") from None
        if connection.closed or connection.info.transaction_status != pq.TransactionStatus.IDLE:
            raise QueueError("completion_failure_unavailable") from None
        return await _record_failure(connection, user_id, table, job, error, role, schema_name)
    logger.info("slow_lane_job_done", user_id=user_id, kind=job.kind, attempts=job.attempts)
    return WorkerResult(status="done", job_id=job.id, attempts=job.attempts)


async def _record_failure(
    connection: AsyncConnection,
    user_id: str,
    table: sql.Identifier,
    job: Job,
    error: BaseException,
    role: str | None = None,
    schema_name: str = "cme_engine",
) -> WorkerResult:
    """Back off, or give up. Only the exception *type* is recorded: a message can carry the turn's text.

    **A stale deletion epoch is terminal at once** (``F5``, ``V2-D106``). Both failure paths fence on
    ``status='running' AND attempts=%s`` so a timed-out worker cannot reset a job already claimed by another worker.
    """
    if isinstance(error, StaleDeletionEpoch):
        return await _discard_stale(connection, user_id, table, job, error, role, schema_name)
    terminal = job.attempts >= MAX_ATTEMPTS
    status = "dead_letter" if terminal else "queued"
    delay = timedelta(0) if terminal else timedelta(seconds=min(300, 2**job.attempts))
    try:
        async for attempt in _contention_retry():
            with attempt:
                async with _tenant(connection, user_id, role):
                    # No `error_type` column exists and none is added: the failure reason is a log line (content-free)
                    # and the attempt count is the durable fact.
                    async with connection.cursor(row_factory=tuple_row) as cursor:
                        await cursor.execute(
                            sql.SQL(
                                "UPDATE {} SET status=%s, next_run_at=pg_catalog.transaction_timestamp() + %s "
                                "WHERE id=%s AND user_id=%s AND status='running' AND attempts=%s "
                                "RETURNING id"
                            ).format(table),
                            (status, delay, job.id, user_id, job.attempts),
                        )
                        if await cursor.fetchone() != (job.id,):
                            raise _LeaseLost
    except _LeaseLost:
        logger.warning("slow_lane_lease_lost", user_id=user_id, kind=job.kind, attempts=job.attempts)
        return WorkerResult(status="idle", job_id=job.id, attempts=job.attempts, reason="lease_lost")
    except Exception as retry_error:
        if _is_retryable_pg_error(retry_error):
            raise QueueError("queue_contention_conflict") from None
        raise
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


async def _discard_stale(
    connection: AsyncConnection,
    user_id: str,
    table: sql.Identifier,
    job: Job,
    stale: StaleDeletionEpoch,
    role: str | None,
    schema_name: str,
) -> WorkerResult:
    try:
        async for attempt in _contention_retry():
            with attempt:
                async with _tenant(connection, user_id, role):
                    async with connection.cursor(row_factory=tuple_row) as cursor:
                        await cursor.execute(
                            sql.SQL(
                                "UPDATE {} SET status='dead_letter', payload=NULL "
                                "WHERE id=%s AND user_id=%s AND status='running' AND attempts=%s "
                                "RETURNING id"
                            ).format(table),
                            (job.id, user_id, job.attempts),
                        )
                        if await cursor.fetchone() != (job.id,):
                            raise _LeaseLost
                        await cursor.execute(
                            sql.SQL(
                                "INSERT INTO {} (id, user_id, memory_ref, event_type, event_at, details) "
                                "VALUES (%s, %s, %s, 'job_discarded_stale_deletion_epoch', "
                                "pg_catalog.transaction_timestamp(), %s)"
                            ).format(sql.Identifier(schema_name, "audit_event")),
                            (
                                f"audit-{uuid4().hex}",
                                user_id,
                                uuid4().hex,
                                Jsonb(
                                    content_free_details(
                                        {
                                            "job_id": job.id,
                                            "kind": job.kind,
                                            "epoch_start": stale.epoch_start,
                                            "current_epoch": stale.current_epoch,
                                        }
                                    )
                                ),
                            ),
                        )
    except _LeaseLost:
        logger.warning("slow_lane_lease_lost", user_id=user_id, kind=job.kind, attempts=job.attempts)
        return WorkerResult(status="idle", job_id=job.id, attempts=job.attempts, reason="lease_lost")
    except Exception as retry_error:
        if _is_retryable_pg_error(retry_error):
            raise QueueError("queue_contention_conflict") from None
        raise
    logger.warning("slow_lane_job_discarded_stale_epoch", user_id=user_id, kind=job.kind)
    return WorkerResult(status="dead_letter", job_id=job.id, attempts=job.attempts, reason="stale_deletion_epoch")


async def recover_stale(
    connection: AsyncConnection,
    user_id: str,
    *,
    schema_name: str = "cme_engine",
    role: str | None = None,
) -> int:
    """Return expired leases with ``FOR UPDATE SKIP LOCKED``, dead-lettering jobs past ``MAX_ATTEMPTS``."""
    _user(user_id)
    table = _schema(schema_name)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise QueueError("recovery_requires_idle_connection")
    outcomes: list[str] = []
    try:
        async for attempt in _contention_retry():
            with attempt:
                async with _tenant(connection, user_id, role):
                    async with connection.cursor(row_factory=tuple_row) as cursor:
                        await cursor.execute(
                            sql.SQL(
                                "WITH stale AS ("
                                " SELECT id, attempts FROM {table}"
                                " WHERE user_id=%s AND status='running'"
                                " AND next_run_at <= pg_catalog.transaction_timestamp()"
                                " ORDER BY next_run_at, id FOR UPDATE SKIP LOCKED"
                                ") "
                                "UPDATE {table} AS j "
                                "SET status = CASE WHEN stale.attempts >= %s THEN 'dead_letter' ELSE 'queued' END, "
                                "next_run_at = pg_catalog.transaction_timestamp(), "
                                "payload = CASE WHEN stale.attempts >= %s THEN NULL ELSE j.payload END "
                                "FROM stale WHERE j.id = stale.id AND j.user_id = %s "
                                "RETURNING j.status"
                            ).format(table=table),
                            (user_id, MAX_ATTEMPTS, MAX_ATTEMPTS, user_id),
                        )
                        outcomes = [str(row[0]) for row in await cursor.fetchall()]
    except Exception as retry_error:
        if _is_retryable_pg_error(retry_error):
            raise QueueError("queue_contention_conflict") from None
        raise
    recovered = len(outcomes)
    if recovered:
        dead_lettered = sum(1 for status in outcomes if status == "dead_letter")
        logger.warning(
            "slow_lane_jobs_recovered",
            user_id=user_id,
            recovered=recovered,
            dead_lettered=dead_lettered,
        )
    return recovered


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


__all__ = [
    "LEASE",
    "RETRYABLE_PG_SQLSTATES",
    "ResultWriter",
    "claim_one",
    "complete_one",
    "enqueue_slow_lane",
    "recover_stale",
]
