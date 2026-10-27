# Temporal query predicates (`RL-043`)

[`predicates.py`](predicates.py) implements Proposal §7.4 and Technical
Specification §3.5 over the canonical `memory_version` table. It provides typed
query objects, composable parameterized SQL and an asynchronous Psycopg executor.
Ranking, fusion and context rendering remain `RL-050`/`RL-051` work.

| Builder | Valid-time selection | Recorded-time selection | Status / guard |
|---|---|---|---|
| `current_state(user_id, reference_time)` | Contains reference time `ρ` | Contains `ρ` | `active` |
| `historical_as_of(user_id, target_time, reference_time)` | Contains target `t` | Contains `ρ` | `active`; `t ≤ ρ` |
| `historical_window(user_id, start, end, reference_time)` | Overlaps `[start, end)` | Contains `ρ` | `active`; `start < end ≤ ρ` |
| `audit_as_of(user_id, target_time, belief_time)` | Contains target `t` | Contains belief time `β` | `active` or `archived`; `β ≤ clock_now()` |

Every predicate binds the exact `user_id`. All modes exclude `candidate`,
`conflict_pending` and `rejected`. PostgreSQL's native `@>` and `&&` operators
decide range membership, including half-open boundaries. No latest-version,
deduplication or ranking rule is added.

**Authority correction:** issue #34's ordinary-query bullets use
`upper_inf(recorded_range)`. The frozen Proposal, Technical Specification and both
43-assertion SQL checks require `recorded_range @> ρ`. This implementation follows
those authoritative references. A query with an earlier reference can return a
now-closed recorded version that was believed at that reference. No specification
change or new research decision is introduced.

## Execute a read

Public instants are timezone-aware Python `datetime` values, normalized to UTC.
Naive inputs and mismatched mode parameters fail validation. Future point/window
requests and future audit beliefs raise `TemporalQueryError`, a Psycopg
`InvalidParameterValue` carrying SQLSTATE `22023` and a static error code. Equality
at `t = ρ`, `end = ρ` and `β = clock_now()` is allowed. Audit valid time is
unrestricted: `t` can exceed `β` and the current clock.

```python
from datetime import UTC, datetime

from implementation.engine.predicates import fetch_versions, historical_as_of

# connection is an existing AsyncConnection for the isolated engine database.
# The authenticated caller establishes app.user_id within its transaction.
async with connection.transaction():
    await connection.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
    query = historical_as_of(
        user_id,
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 10, 1, tzinfo=UTC),
    )
    result = await fetch_versions(connection, query, schema_name="cme_engine")
    versions = result.for_answer_context()
```

The executor requires the existing `app.user_id` to match the query exactly;
unset or different identity raises `InsufficientPrivilege` (`42501`). It does
not change tenant/clock GUCs or privileges. The table and `clock_now()` are
schema-qualified in the requested isolated `cme_...` namespace. Use runtime
credentials with the schema's RLS policies; this helper does not grant roles.

For audit, the executor reads that schema's `clock_now()` inside the read
transaction before executing the predicate. Replay `app.clock` and PostgreSQL's
transaction-clock fallback therefore use the existing schema contract. It does
not substitute a Python wall clock or advance the clock. Invalid audit requests
fail even when no row could match.

An existing caller transaction is preserved through a nested savepoint; the
helper neither commits it nor closes the connection. Without an existing
transaction, it owns only its read transaction. Retrieval/ranking callers must
compose their operations in the consistent snapshot required by their own
contract. Clock monotonicity and write sequencing remain `RL-044` work.

## Compose SQL

`compile_predicate(query, alias="m")` returns `CompiledPredicate.statement`
(a Psycopg composable WHERE clause) and immutable `.parameters`. Pass them
separately to Psycopg; values are never interpolated into SQL. Column aliases use
quoted identifiers. The caller must keep the full user/status/temporal clause
when composing a retrieval query.

An audit predicate additionally requires `database_clock=<aware datetime>` from
the database transaction. The pure compiler checks this value but cannot
authenticate its origin. Prefer `fetch_versions` when a caller does not already
own that database-clock check. Both compilation and execution revalidate query
fields, including objects produced through Pydantic copy/construct helpers.

The builders install no persistent SQL functions and change no DDL, migration
inventory, dependency lock or root application code.

## Results and audit separation

`TemporalResult` tags the mode and exact user and contains a tuple of
`TemporalVersion` objects with every canonical memory-version field. Ordering
is deterministic by version ID and does not imply relevance. `TimeRange`
preserves lower/upper instants and inclusion flags. An unbounded upper endpoint
is `None`; PostgreSQL's literal timestamp infinities are the explicit strings
`"infinity"`/`"-infinity"`. They remain distinct across JSON serialization and
session timezone/DateStyle settings.

`result.for_answer_context()` permits ordinary results and rejects audit results
with `audit_not_for_answer_context`. Inspectors can access audit `.versions`.
Answer/context consumers must use this guarded path and must not flatten audit
rows into answering context. These Python types are not an authorization
boundary against arbitrary trusted code or direct SQL.

## Verification

```console
uv run --no-sync pytest -q implementation/tests/test_predicates.py
# Set CME_TEST_DATABASE_URL to a disposable, isolated database with the shared
# extensions initialized by tests/init_extensions.sql. Fixtures create/drop
# unique schemas and one temporary unprivileged role.
uv run --no-sync pytest -q implementation/tests/test_predicates_db.py
```

The DB suite uses the full canonical schema with provenance triggers enabled,
evidence-backed synthetic E1–E3 rows, actual RLS under a non-superuser role,
privileged explicit-user-filter controls, both clocks, half-open boundaries,
status visibility, transaction ownership and infinity round trips. See
[`../../evidence/RL-043-verification.md`](../../evidence/RL-043-verification.md)
for the contribution's verification record and its limits.

---

# Transactional supersession and archival (`RL-042`)

[`transactions.py`](transactions.py) implements Proposal §§7.2, 8.2, 10.2 and Technical
Specification §§3.4, 6 over the canonical `memory_version`, `memory_evidence`,
`memory_derivation`, `memory_conflict`, and `audit_event` tables:

- **`stage_candidate`**: Stages a non-believed row (`candidate`, `conflict_pending`, or `rejected`) with `recorded_range = NULL` (`R4`).
- **`commit_initial_version`**: Commits an initial or additive `active` version at `recorded_range = [c, NULL)` with provenance (`R1`, `R3`, `R4`, `Invariant 8`).
- **`supersede_single`**: Single-valued close-then-insert supersession (`R1`–`R3`, `E1`, `E2`), handling `v_old < v_new` (truncated re-issue + copied provenance), `v_old == v_new` (same-instant correction without empty re-issue), `v_new < v_old` (hold in `conflict_pending` + `memory_conflict`), and ablation modes `A1` (`P-flat`), `A2` (`P-nohistory`), and `A2b` (`D-15`, disabled unless `allow_a2b=True`).
- **`revise_transition_boundary`**: Closes both adjacent active versions at `c` and re-issues both with the revised shared valid-time boundary (`E3`).
- **`resolve_conflict_pending`**: Resolves a `conflict_pending` or `candidate` row to `active` (setting `recorded_range = [c, NULL)` at the resolution transaction's clock `c` while closing/re-issuing any overlapping active version) or `rejected` (`R4`).
- **`end_multivalued`**: Closes an open `single_valued = False` version at `c` and inserts a truncated `[v_old, valid_end)` version with copied provenance (`R7`).
- **`archive_version` & `ensure_archival_trigger`**: Enforces `R5`, `Invariant 10`, and `D-27` under per-user transaction advisory locks and row locks (`FOR UPDATE`), preferring branch `'S'` when both `(S)` and `(C)` hold, rejecting `T1`/`T2` when neither holds, and preventing replacement mutations from uncovering an already-archived `(C)`-branch source.

