"""Tenant-scoped atomic queue execution under the D-18 retry protocol."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from re import fullmatch

from psycopg import AsyncConnection, pq, sql
from psycopg.errors import DeadlockDetected, ExclusionViolation, InsufficientPrivilege, SerializationFailure
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from implementation.clock import ClockTick, allocate_clock
from implementation.queue.contracts import EnqueueResult, Job, JobIdentityConflict, NewJob, QueueError, WorkerResult

type JobHandler = Callable[[AsyncConnection, Job, ClockTick], Awaitable[None]]
MAX_ATTEMPTS = 3


def _schema(value: str) -> sql.Identifier:
    if not isinstance(value, str) or len(value) > 63 or fullmatch(r"cme_[a-z][a-z0-9_]*", value) is None:
        raise QueueError("isolated_engine_schema_identifier_required")
    return sql.Identifier(value, "job")


def _user(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise QueueError("nonblank_user_id_required")


async def _context(connection: AsyncConnection, user_id: str) -> None:
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            "SELECT NULLIF(pg_catalog.current_setting('app.user_id',true),''), "
            "pg_catalog.current_setting('transaction_isolation')"
        )
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("queue_user_context_mismatch")
        if row[1] != "read committed":
            raise QueueError("queue_requires_read_committed")


async def _lock_user(connection: AsyncConnection, user_id: str) -> None:
    await _context(connection, user_id)
    await connection.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (user_id,))


def _work_identity(job: Job | NewJob) -> tuple[object, ...]:
    return job.user_id, job.kind, job.turn_id, job.extractor_version, job.candidate_fingerprint


async def enqueue(connection: AsyncConnection, job: NewJob, *, schema_name: str = "cme_engine") -> EnqueueResult:
    """Enqueue once through either unique identity, preserving existing work/state."""
    job = NewJob.model_validate(job.model_dump())
    table = _schema(schema_name)
    params = job.model_dump()
    params["payload"] = None if job.payload is None else Jsonb(job.payload)
    async with connection.transaction():
        await _context(connection, job.user_id)
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {table} (id,user_id,kind,turn_id,idempotency_key,status,attempts,next_run_at,"
                    "payload,extractor_version,candidate_fingerprint) VALUES ("
                    "%(id)s,%(user_id)s,%(kind)s,%(turn_id)s,%(idempotency_key)s,'queued',0,"
                    "COALESCE(%(not_before)s::timestamptz,pg_catalog.transaction_timestamp()),%(payload)s,"
                    "%(extractor_version)s,%(candidate_fingerprint)s) ON CONFLICT DO NOTHING RETURNING *"
                ).format(table=table),
                params,
            )
            row = await cursor.fetchone()
            if row is not None:
                return EnqueueResult(job=Job.model_validate(row), created=True)
            await cursor.execute(
                sql.SQL(
                    "SELECT *, payload IS NOT DISTINCT FROM %(payload)s::jsonb AS same_payload FROM {table} "
                    "WHERE user_id=%(user_id)s AND (id=%(id)s OR idempotency_key=%(idempotency_key)s OR "
                    "(turn_id=%(turn_id)s AND extractor_version=%(extractor_version)s "
                    "AND candidate_fingerprint=%(candidate_fingerprint)s)) ORDER BY id"
                ).format(table=table),
                params,
            )
            matches = await cursor.fetchall()
            if len(matches) != 1:
                raise JobIdentityConflict("job_identity_conflict")
            match = matches[0]
            same_payload = match.pop("same_payload")
            existing = Job.model_validate(match)
            if not same_payload or _work_identity(existing) != _work_identity(job):
                raise JobIdentityConflict("job_identity_conflict")
            return EnqueueResult(job=existing, created=False)


def is_transaction_retryable(error: BaseException) -> bool:
    return isinstance(error, SerializationFailure | DeadlockDetected)


@dataclass
class AttemptState:
    selected: Job | None = None
    attempts: int = 0
    transaction_attempts: int = 0


async def _selected_job(
    connection: AsyncConnection, table: sql.Identifier, user_id: str, state: AttemptState
) -> Job | None:
    pinned = state.selected
    async with connection.cursor(row_factory=dict_row) as cursor:
        if pinned is None:
            await cursor.execute(
                sql.SQL(
                    "SELECT * FROM {} WHERE user_id=%s AND status='queued' "
                    "AND next_run_at<=pg_catalog.transaction_timestamp() "
                    "ORDER BY next_run_at,id FOR UPDATE SKIP LOCKED LIMIT 1"
                ).format(table),
                (user_id,),
            )
        else:
            # A retry may wait for an intervening winner, but never selects a new job.
            await cursor.execute(
                sql.SQL(
                    "SELECT *, COALESCE(payload,'null'::jsonb) = %s::jsonb AS same_payload "
                    "FROM {} WHERE user_id=%s AND id=%s AND idempotency_key=%s FOR UPDATE"
                ).format(table),
                (Jsonb(pinned.payload), user_id, pinned.id, pinned.idempotency_key),
            )
        row = await cursor.fetchone()
        if row is None:
            return None
        same_payload = row.pop("same_payload", True)
        selected = Job.model_validate(row)
        if pinned is not None and (not same_payload or _work_identity(selected) != _work_identity(pinned)):
            raise QueueError("pinned_job_identity_changed")
        return selected


def _terminal(job: Job, state: AttemptState) -> WorkerResult:
    if job.status in ("done", "dead_letter"):
        return WorkerResult(
            status=job.status,
            job_id=job.id,
            attempts=job.attempts,
            transaction_attempts=state.transaction_attempts,
            reason="already_terminal",
        )
    return WorkerResult(
        status="idle",
        job_id=job.id,
        attempts=job.attempts,
        transaction_attempts=state.transaction_attempts,
        reason="job_unavailable",
    )


async def _attempt(
    connection: AsyncConnection,
    user_id: str,
    handler: JobHandler,
    table: sql.Identifier,
    schema_name: str,
    replay_time: datetime | None,
    state: AttemptState,
) -> WorkerResult:
    async with connection.transaction():
        await _lock_user(connection, user_id)
        job = await _selected_job(connection, table, user_id, state)
        if job is None:
            return WorkerResult(status="idle", transaction_attempts=state.transaction_attempts)
        if job.status != "queued":
            return _terminal(job, state)
        if state.selected is None:
            state.selected = job.model_copy(deep=True)
            state.attempts = job.attempts
        state.attempts = max(state.attempts, job.attempts)
        if state.attempts >= MAX_ATTEMPTS:
            await connection.execute(
                sql.SQL("UPDATE {} SET status='dead_letter',attempts=%s WHERE id=%s AND user_id=%s").format(table),
                (state.attempts, job.id, user_id),
            )
            return WorkerResult(
                status="dead_letter",
                job_id=job.id,
                attempts=state.attempts,
                transaction_attempts=state.transaction_attempts,
                reason="retry_budget_exhausted",
            )
        state.attempts += 1
        await connection.execute(
            sql.SQL("UPDATE {} SET status='running',attempts=%s WHERE id=%s AND user_id=%s").format(table),
            (state.attempts, job.id, user_id),
        )
        tick = await allocate_clock(connection, user_id, schema_name=schema_name, replay_time=replay_time)
        await handler(connection, job, tick)
        completed = await _selected_job(connection, table, user_id, state)
        if completed is None or completed.status != "running" or completed.attempts != state.attempts:
            raise QueueError("job_completion_identity_changed")
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "UPDATE {} SET status='done' WHERE id=%s AND user_id=%s "
                    "AND idempotency_key=%s AND status='running' AND attempts=%s RETURNING id"
                ).format(table),
                (job.id, user_id, job.idempotency_key, state.attempts),
            )
            if await cursor.fetchone() != (job.id,):
                raise QueueError("job_completion_identity_changed")
        outcome = WorkerResult(
            status="done",
            job_id=job.id,
            attempts=state.attempts,
            transaction_attempts=state.transaction_attempts,
            clock=tick,
        )
    # Return only after commit; a commit-time transaction conflict is retryable too.
    return outcome


async def _finalize_failure(
    connection: AsyncConnection,
    user_id: str,
    table: sql.Identifier,
    state: AttemptState,
    reason: str,
) -> WorkerResult:
    assert state.selected is not None
    async with connection.transaction():
        await _lock_user(connection, user_id)
        job = await _selected_job(connection, table, user_id, state)
        if job is None:
            return WorkerResult(status="idle", transaction_attempts=state.transaction_attempts, reason="job_removed")
        if job.status != "queued":
            return _terminal(job, state)
        # A finalizer cannot overwrite another worker's completed transaction.
        attempts = max(job.attempts, state.attempts)
        await connection.execute(
            sql.SQL("UPDATE {} SET status='dead_letter',attempts=%s WHERE id=%s AND user_id=%s").format(table),
            (attempts, job.id, user_id),
        )
        result = WorkerResult(
            status="dead_letter",
            job_id=job.id,
            attempts=attempts,
            transaction_attempts=state.transaction_attempts,
            reason=reason,
        )
    return result


async def process_one(
    connection: AsyncConnection,
    user_id: str,
    handler: JobHandler,
    *,
    schema_name: str = "cme_engine",
    replay_time: datetime | None = None,
) -> WorkerResult:
    """Own fresh whole transactions; handler must perform only database work.

    Prepared provider/gate work belongs outside this closure. Do not issue SQL
    transaction control, close the connection or allocate a second clock in it.
    Cancellation propagates after rollback; it never consumes a durable job.
    """
    _user(user_id)
    table = _schema(schema_name)
    if replay_time is not None:
        if not isinstance(replay_time, datetime) or replay_time.tzinfo is None or replay_time.utcoffset() is None:
            raise QueueError("timezone_aware_replay_instant_required")
        replay_time = replay_time.astimezone(UTC)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise QueueError("worker_requires_idle_connection")
    state = AttemptState()
    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(lambda error: is_transaction_retryable(error) and state.attempts < MAX_ATTEMPTS),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=wait_random_exponential(multiplier=0.1, max=2),
            reraise=True,
        ):
            state.transaction_attempts = attempt.retry_state.attempt_number
            with attempt:
                return await _attempt(connection, user_id, handler, table, schema_name, replay_time, state)
    except Exception as error:
        if state.selected is None:
            raise  # Unselected/precondition failures cannot consume a job.
        if connection.closed or connection.info.transaction_status != pq.TransactionStatus.IDLE:
            raise QueueError("worker_failure_finalization_unavailable") from None
        reason = (
            "transaction_retry_exhausted"
            if is_transaction_retryable(error)
            else ("semantic_exclusion_conflict" if isinstance(error, ExclusionViolation) else "job_failed")
        )
        return await _finalize_failure(connection, user_id, table, state, reason)
    raise QueueError("worker_attempts_unavailable")
