# Transaction clock (`RL-044`)

`allocate_clock` supplies one recorded instant for a caller-owned mutation
transaction. It implements Proposal §7.4 R1/R3 and the selected D-18 protocol;
it does not replace the canonical schema's `clock_now()` function.

```python
from custom_memory_engine.clock import allocate_clock

async with connection.transaction():
    # The authenticated caller establishes this identity before engine work.
    await connection.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
    tick = await allocate_clock(connection, user_id, schema_name="cme_engine")
    # Every close/reissue/replacement statement now uses cme_engine.clock_now().
    # Reuse tick for the entire operation; do not allocate again in this transaction.
```

The public API is:

```python
await allocate_clock(connection, user_id, *, schema_name="cme_engine", replay_time=None)
```

It returns immutable `ClockTick(instant, raw_instant, mode, advanced)` values.
All instants are timezone-aware Python datetimes, normalized to UTC. A replay
harness explicitly supplies `replay_time`; live callers leave it absent. The
pure helper `advance_instant(raw_instant, previous=None)` implements
`max(raw_instant, previous + 1 microsecond)`, including ties and out-of-order
raw instants. Unrepresentable UTC conversions or exhausted Python datetime
values raise a static `ClockError`, never wrap around.

## Transaction and identity contract

- An existing active **READ COMMITTED** transaction and matching preexisting
  `app.user_id` are required. The helper neither changes the tenant nor opens
  an independent top-level transaction, commits or closes the connection.
- It acquires `pg_advisory_xact_lock(hashtext(user_id))` before any memory-clock
  reads. Allocate before the caller reads/mutates memory; the surrounding writer
  must follow the same per-user protocol. Hash collisions can serialize distinct
  users safely; this is not a promise of perfect cross-user parallelism.
- Live raw time is the database's `transaction_timestamp()`. An inherited
  `app.clock` is never taken as raw live time. Replay raw time comes only from
  the explicit aware `replay_time` argument. This API separation is a trusted
  caller contract, not an authorization mechanism against arbitrary Python/SQL.
- The allocated instant is set with transaction-local `app.clock`, and the
  schema-qualified `clock_now()` result is checked for equality before success.
  All R1 statements share it. A second allocation for the same user in one
  transaction raises `clock_already_allocated_in_transaction`, even when the raw
  time is identical. Callers pass the first tick through their whole operation.
- A savepoint keeps a rejected allocation atomic while preserving the caller's
  outer transaction. Normal outer commit/rollback restores local GUCs; rollback
  also removes the allocation audit event. The helper performs no hidden retry.

Unset or different tenant context raises `InsufficientPrivilege` (`42501`).
Clock validation errors use `ClockError`, a Psycopg `InvalidParameterValue`
with SQLSTATE `22023` and a static `.code` that does not echo input values.

## Durable watermark without new DDL

The allocator uses the existing `AUDIT_EVENT` table for a content-free
`clock_allocated` event. `id` and `memory_ref` are random identifiers;
`event_at` holds the allocated instant, and `details` contains only
`clock_mode` (`live`/`replay`) and the full `pg_current_xact_id()` value. The
full transaction ID also makes repeated-allocation detection survive normal
Python object or connection-wrapper changes within the same transaction.

The next allocation observes the user's maximum prior allocation event and
all existing memory recorded lower/finite upper bounds. This synchronizes with
previously written rows, persists across new connections/processes, and remains
monotonic when an allocation creates no memory row or all memory is later
deleted. Mode attribution uses `(user_id, event_at = recorded_from)`; no content,
evidence span or fact identifier enters the clock event.

Literal negative-infinity lower bounds are below every finite instant. A
positive-infinite clock event or recorded lower bound is rejected because it
cannot be advanced by a finite clock. Unbounded and literal-infinity upper
range endpoints describe open-ended beliefs and do not act as clock watermarks.

Allocation audit rows are operational clock state. Any future retention/purge
implementation must preserve an equivalent per-user high-water mark before
removing them. This contribution introduces no table, index, persistent SQL
function, migration inventory or schema-fingerprint change. Production runtime
grants and scale/retention profiling remain separate tasks.
