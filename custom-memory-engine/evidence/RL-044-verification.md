# RL-044 clock and queue — verification record

27 October 2026. Issue [#35](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/35).
Base: `20e206c320ce74dabcc0061f8413531c735414b2`. All contribution files are inside
`custom-memory-engine/`; canonical schema, migrations, root product, manifests and
dependency locks are unchanged.

## Contract and authority

[`implementation/clock`](../implementation/clock/README.md) allocates one strictly
increasing recorded instant per user/transaction. Live raw time comes from the
database transaction clock; replay input is explicit and UTC-normalized. Native
advisory locking, a durable content-free audit watermark and existing memory
recorded bounds implement `max(raw, previous + 1µs)` across ties, out-of-order
input, new connections/processes and empty jobs. The allocated instant is checked
against the schema-qualified `clock_now()` and shared by all R1 statements.

[`implementation/queue`](../implementation/queue/README.md) supplies typed enqueue
and tenant-scoped processing of prepared database-only handlers. Due jobs use
native SKIP LOCKED claims after the canonical user advisory lock. Effects,
clock allocation and completion commit atomically. D-18 whole-transaction retries
cover SQLSTATE40001/40P01, including commit, with at most three attempts and
randomized exponential backoff (0.1s multiplier, 2s maximum). Persisted prior
attempts count toward that fixed budget. Semantic23P01 is not blindly retried.

Frozen Proposal §7.4 requires one transaction instant, and the merged canonical
`clock_now()` uses `transaction_timestamp()`. This takes precedence over stale
Technical Specification §8's `clock_timestamp()` fallback text. No changing
per-statement clock is substituted. The canonical twelve-table schema represents
dead letters as `JOB.status='dead_letter'`; no extra domain table is introduced.

## Requirement-to-evidence map

| Requirement / risk | Direct evidence |
|---|---|
| Live/replay monotonicity, equality, out-of-order, timezone and exhaustion |31 pure clock cases; native persisted ties/out-of-order, independent users, existing recorded lower/closed upper bounds and fresh Python process |
| One instant per transaction, mode attribution and GUC restoration |Actual schema clock equality, repeated-allocation rejection, live transaction timestamp despite stale session override, content-free mode/transaction metadata, rollback/local clock restoration |
| Persistent watermark without memory rows |Committed empty allocations advance across new connections/processes; rolled-back allocation does not consume a durable instant |
| Typed exact enqueue identity and safe SQL |32 pure queue cases; bound identifiers/values; actual primary/global idempotency/extraction tuple collision and concurrent enqueue controls; no reset of completed work |
| Native SKIP LOCKED and per-user serialization |Held row-lock control skips to next due row; real `pg_locks` observations establish same-user waiting and other-user progress with two workers |
| Whole-transaction retry and zero duplicate committed effects |40001/40P01 after a synthetic memory insert; fresh transaction IDs; one committed memory/evidence/clock event plus done job; redelivery does not execute again |
| Commit-time conflicts |Deferred constraint trigger raises40001 at the first two commits via a nontransactional test sequence; third succeeds with three fresh transactions and exactly one committed effect |
| Exhaustion, prior budgets, permanent conflicts and cancellation |Three-attempt dead letter; persisted attempts2/3 permit only1/0 further callbacks; actual23P01 is not retried; cancellation rolls memory/job/clock back and permits safe retry |
| Competing worker and pinned work |Actual lock-controlled winner finishes original job during backoff; retry returns that terminal row rather than selecting another; native JSONB/logical identity guard refuses changed work |
| Completion row ownership |Handler deletes its own queued completion row after inserting memory; guarded completion fails, entire attempt rolls back and restored job dead-letters with zero partial memory/clock effects |
| Tenant/role/transaction boundaries |Actual non-superuser/NOBYPASSRLS role; mismatched/unset identity refusal; foreign global idempotency collision does not disclose the row; nonidle worker and wrong isolation rejected |

Native fixtures use the unchanged full twelve-table DDL, real user/session/turn
support and `MEMORY_EVIDENCE`. Deferred provenance enforcement stays enabled.
Expected committed row counts and clock instants derive from R1/R3/Invariant9,
not from a mock database or the worker's private call structure. Test-only
sequences/triggers for fault injection are confined to unique owned schemas.

## Executed verification

Windows CPython3.12.12 with frozen engine dependencies and baseline extras;
PostgreSQL16.15, vector0.8.7 and btree_gist1.7 from the pinned Compose digest.
Full baseline software integration also uses pinned FalkorDB6.0.3 and the
existing immutable tokenizer/embedding assets. No paid model/provider call.

| Check | Observed result |
|---|---|
| Initial pure clock/queue contracts |63 passed (31 clock,32 queue), also independently executed |
| Initial native clock/queue suite |85 passed, including22 PostgreSQL cases;43/43 canonical SQL assertions |
| Adversarial regression demonstration before fixes |89 collected:86 passed,3 failed, zero skips. Failures directly exposed missing completion ownership and changed pinned JSONB/kind handling |
| Corrected focused suite, including persisted budget cases |**91/91 passed**, including **28 native DB cases**, zero skips/failures/collection errors; **43/43 SQL assertions** |
| Complete local service-backed workspace |**768/768 passed**, including **70/70 integration cases**, zero skips/failures/collection errors; **43/43 SQL assertions** |
| Separate frozen default installation |**668 passed,84 explicit skips**, no failures/collection errors.81 test-level skips plus3 module skips; optional vendor/model packages confirmed absent |
| Engine Ruff/format/Pyright |Pass;0 type errors |
| Root Ruff/format/Pyright with its existing interpreter |Pass;0 type errors; no root product modifications |
| Five canonical-LF hashes, mechanical hard checks, exact CP stdout |Pass; `ALL HARD CHECKS PASSED`; exact CP output equals committed reference |
| Scope, imports and documentation |All changed files in engine workspace; unchanged DDL/migration/catalog/locks; no root cross-imports; internal guide links resolve |

Independent review first reproduced the JSON boolean/numeric ambiguity in the
pinned retry guard: Python considers `True == 1`, whereas PostgreSQL JSONB
distinguishes them. Native regressions then demonstrated both that path and a
missing finalizer identity check. The shared row reader now checks native JSONB
and logical work identity before retry/finalization. Completion is revalidated
after the handler and requires a matching running row/attempt/key through
conditional UPDATE RETURNING before commit. All three failing regressions pass
after these fixes; the earlier failing evidence was retained.

A separate final source/oracle review inspected the corrected guards and the
actual91-test native result. It reports no remaining material source/API/oracle
finding. Its fresh pure execution also passes63 cases. Root Ruff's older version
required a local fixture bridge instead of a redundant imported fixture alias;
the bridge preserves async generator cleanup. Submitted-head CI verifies the
final committed tree, including that compatibility-only fixture change.

## Ownership, cleanup and limits

Every owned PostgreSQL run removes its unique containers, networks and volumes;
final counts are **0/0/0**. The full run verifies the Falkor graph list is empty
and removes its uniquely labelled container. An initial temporary Falkor startup
failed before PostgreSQL provisioning; that container was removed. The external
runner now waits for bounded health readiness; the final full run succeeds.
No pre-existing project database/container/graph was selected for removal.

These are synthetic software checks. Provider/extraction/gate/writer/deletion
implementations remain separate. Handlers must recheck future deletion-epoch
requirements inside their mutation transaction and avoid external effects or
transaction/queue/clock control. Future replay adapters must retain the allocated
instant for reference clamps and queue-drain barriers. Wider runtime grants,
all-engine replay integration and eight-worker/HNSW load gates are not completed
here. Watermark retention must preserve equivalent per-user state before audit
rows are purged. No latency/production readiness or H1–H4 research claim is made.
Human leader review and merge remain required.

Primary implementation references:
[PostgreSQL locking SELECT](https://www.postgresql.org/docs/16/sql-select.html),
[Psycopg transaction ownership](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[Tenacity retry controls](https://tenacity.readthedocs.io/en/latest/).
