# Tenant-scoped slow-path queue (`RL-044`)

This package executes prepared database mutations over the canonical `JOB`
table. It implements `SELECT ... FOR UPDATE SKIP LOCKED`, the D-18 per-user
advisory lock and bounded whole-transaction Tenacity retries. It neither calls
providers nor implements the later extraction/gate/writer/deletion algorithms.

## Prepare, enqueue and process

Prepare external/model work before calling the worker. The handler performs only
database operations in the supplied transaction, using its `ClockTick` and the
schema's `clock_now()` for every R1 statement. It must not issue transaction
control, mutate queue/clock bookkeeping, close the connection or allocate a second
clock. Trusted arbitrary SQL is outside this callback contract.

```python
from custom_memory_engine.queue import NewJob, enqueue, process_one

# connection is an isolated engine AsyncConnection, normally autocommit=True.
# The authenticated caller establishes its exact tenant context first.
await connection.execute("SELECT set_config('app.user_id', %s, false)", (user_id,))
request = NewJob(
    id=job_id,
    user_id=user_id,
    kind="commit_prepared_candidate",
    idempotency_key=stable_operation_key,
    turn_id=source_turn_id,
    extractor_version=frozen_extractor_version,
    candidate_fingerprint=stable_candidate_fingerprint,
    payload=prepared_database_input,
)
receipt = await enqueue(connection, request, schema_name="cme_engine")
outcome = await process_one(connection, user_id, prepared_database_handler, schema_name="cme_engine")
```

The handler signature is `async (connection, job: Job, tick: ClockTick) -> None`.
It receives the full claimed job and the one allocated recorded instant. Queue
state, allocation event and handler writes commit atomically. Deferred constraint
failures at commit are part of that same attempt. Cancellation/crash before
commit rolls back the uncommitted `running` transition, leaving the job queued;
there is no durable running lease to expire. A lost response after a successful
commit is safe to redeliver because the durable job is already `done`.

Slow-path jobs whose prepared input depends on memory state may include an integer
`deletion_epoch` in an object-shaped payload. The worker compares it with the
current per-user deletion epoch before invoking the handler. A stale job is not
run, charged an attempt, or dead-lettered; `StaleDeletionEpoch` propagates with
the captured and current epochs so the caller can prepare fresh input against
surviving data. The failed transaction leaves the job queued.

`process_one` owns its root transactions and requires an **idle connection**.
It rejects an existing caller transaction rather than committing it or retrying
only a savepoint. `enqueue` can compose through a nested savepoint and preserves
an existing caller transaction. Both require matching, pre-existing `app.user_id`
and READ COMMITTED isolation; neither changes tenant identity or role. Caller
row factories and connection lifetime are preserved. Future runtime role grants
remain `RL-071` work; a worker is explicitly scoped to one tenant per call.

## Identity and retry behavior

`NewJob` validates nonblank exact identity strings, timezone-aware `not_before`
and complete extraction identity when an extractor/fingerprint is supplied.
Values are bound separately from SQL; requested schemas are quoted, isolated
`cme_...` namespaces. Queue payloads belong in `JOB`, never in clock/error logs.

The database's global `idempotency_key` and extraction identity
`(user_id, turn_id, extractor_version, candidate_fingerprint)` both prevent
duplicate enqueue. An equivalent request returns `EnqueueResult(created=False,
job=<current row>)`; it never resets status, attempts or schedule. Different
work under an occupied identity raises `JobIdentityConflict` (`23505`) without
returning another user's row. Payload equivalence uses native JSONB comparison,
so boolean `true` is distinct from numeric `1`. The first accepted schedule wins.

Each processing attempt checks tenant scope, acquires
`pg_advisory_xact_lock(hashtext(user_id))`, then claims a due queued row in stable
`next_run_at, id` order with `FOR UPDATE SKIP LOCKED`. All memory reads/mutations
come after the advisory lock. Hash collisions can serialize unrelated tenants;
this is safe and does not promise perfect cross-user parallelism.

Only SQLSTATE **40001** / **40P01** are retried: at most **three** fresh whole
transactions, using Tenacity randomized exponential backoff (multiplier **0.1s**,
maximum **2s**), retaining the same selected job ID and idempotency key. The fixed
job attempt budget includes previously persisted attempts. Backoff happens after
rollback, without holding the transaction lock. No provider/external side effect
is repeated inside that closure.

On retry, the pinned job is re-read under lock. A concurrent winner's terminal
state is returned; the worker never switches to a different job. Unexpected
changes to pinned work fail closed. Exhausted transaction failures and permanent
handler errors roll back all work, then finalize that selected job as
`dead_letter` in a new locked transaction. A finalizer rechecks the row and cannot
overwrite another worker's `done` state. Semantic **23P01** exclusion conflicts
are not blindly retried: they produce a structured dead-letter reason for later
conflict handling. `JOB.status='dead_letter'` is the canonical dead-letter
representation; no thirteenth domain table is introduced.

`WorkerResult` exposes `status` (`idle`, `done`, `dead_letter`), `job_id`, durable
`attempts`, per-call `transaction_attempts`, a static `reason` and the successful
allocation in `clock`. Outcome reasons contain static codes rather than the
callback exception or payload; precondition/database errors may propagate.
If a broken connection prevents durable finalization, a static error is raised;
the API does not falsely claim that a dead-letter transition was persisted.

## Two distinct clocks and future integration

Due selection uses actual PostgreSQL transaction time and optional `not_before`;
historical replay instants do not control scheduling. Pass `replay_time=<aware
datetime>` only from the replay harness to allocate recorded time; normal live
mode obtains its raw instant from the database transaction clock. See the
[`clock guide`](../clock/README.md) for persisted monotonic watermarks, replay
attribution and allocation-once rules.

The future adapter must retain the returned allocated `clock.instant` and clamp
question/audit reference time to it, because repeated/out-of-order raw timestamps
can be advanced by 1µs. It must wait for queued work to settle before benchmark
questions. Prepared-work handlers must recheck the relevant deletion epoch in
their mutation transaction when the later deletion/gate integration is added.
This package does not pretend those future components or the `RL-093` eight-worker
load/HNSW benchmark are complete.

Run pure contracts with `pytest tests/integration/test_queue.py` and actual
disposable PostgreSQL cases with
`pytest tests/integration/test_clock_queue_db.py`. The DB URL must be explicit
through `CME_TEST_DATABASE_URL`; fixtures use canonical DDL with real provenance,
RLS, unique schemas and owned cleanup. No migration/catalog/schema change is
needed. Full contribution evidence is recorded in
[`../../../evidence/RL-044-verification.md`](../../../evidence/RL-044-verification.md).
