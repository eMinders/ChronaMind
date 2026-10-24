# RL-012 — Database compatibility and concurrency (#2)

Prepared 2026-10-24. Technical selection verified by compatibility probes; Member 4 / Member 2 review pending.

## D-25 selected pin

- PostgreSQL **16.15**, pgvector **0.8.7**, btree_gist **1.7**.
- Tag: `pgvector/pgvector:0.8.7-pg16-bookworm`.
- Immutable registry digest: `sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a`.
- Reference: `pgvector/pgvector:0.8.7-pg16-bookworm@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a`.

Verified via Docker pull/inspect and SQL against a fresh container from this digest (see evidence report). Record platform, image ID, registry digest and SQL versions together; a tag alone is not a pin. RL-004 should reuse this reference. Patch/security updates require a new recorded digest and repeated compatibility checks, not indefinite deployment of an old pin.

Research Lab `reference/Source_Evidence_Ledger.md` rows **[38] and [39]** establish range/exclusion/queue semantics and post-filtering risk in approximate indexes. The saved `validation/bitemporal_checks.output.txt` is a **43-assertion reduced-schema baseline** on 16.15, not evidence for RLS, vector search, concurrency, or production readiness. It archives an un-replaced current belief deliberately to demonstrate status filtering; that is **not** V4-safe archival. We rerun it separately, without altering or relabeling its scope.

`tests/test_database_compatibility.py` additionally checks native 384-dimensional vector/range adaptation, HNSW creation and iterative-scan GUC acceptance, pooled transaction-local clock commit/rollback cleanup, exact five-column exclusion and close-then-insert, two-connection advisory/SKIP LOCKED behavior, and conflicting writer exclusion. HNSW recall and eight-worker scalability remain RL-093, not inferred from a one-vector probe. Exact filtered vector search remains default.

## D-18 concurrency protocol

READ COMMITTED slow-path transaction, **first** acquire `pg_advisory_xact_lock(hashtext(user_id))`; all same-user supersession, promotion, consolidation, archival and deletion writers follow the same convention. `hashtext` collisions can serialize unrelated users: safe but not a claim of perfect cross-user parallelism. Multi-user operations are prohibited on slow path; administrative batches take locks in a deterministic order.

Database backstop (RL-040):

```sql
EXCLUDE USING gist (
  user_id WITH =, subject_key WITH =, predicate_key WITH =,
  valid_range WITH &&, recorded_range WITH &&
) WHERE (single_valued AND status IN ('active','archived'))
```

Canonical keys/cardinality and NOT NULL/nonempty/half-open range checks are mandatory; exemption must not be user-controlled. Close old recorded ranges at one strictly increasing transaction clock, then insert reissues/replacements (R1/R3). Row locks supplement, not replace, transaction locks. Rolled-back transitions leave no committed partial state.

### Measured fallback, not automatic escalation

RL-093 runs eight workers with same-user and cross-user workloads, deletion races and canonical conflict keys. Record lock wait time, deadlocks, serialization/exclusion failures, queue lag, retry counts, throughput and p50/p95 latency; verify all invariants and no lost/duplicate commits. No numeric latency SLO is invented before pilot.

If contention appears, first diagnose hot tenants, long transactions, key collisions and missing lock protocol. **SERIALIZABLE does not remove GiST locks or guarantee better throughput.** Per issue's promotion requirement, evaluate SERIALIZABLE as a separately measured candidate; promote only after documented correctness benefit and acceptable pilot latency/retry behavior. Take the advisory lock before reads, keep external/model calls outside transactions, recheck deletion epoch inside each attempt.

Bounded **whole-transaction** tenacity retries for SQLSTATE `40001` (serialization failure) and `40P01` (deadlock): at most 3 attempts, randomized exponential backoff, multiplier 0.1 s, max 2 s. Retry with fresh transaction/reads and same idempotency key; never retry a statement in an aborted transaction. `23P01` is a semantic exclusion conflict: route to conflict handling, not blind retries. Persistent failures surface a structured content-free exception/dead-letter outcome. Providers or external side effects are not repeated inside the DB retry closure.

## Impact and sign-off

RL-004, RL-040, RL-042 and RL-093 have a concrete verified target. This is not the 12-table DDL, RLS implementation or eight-worker load test. Owner Member 4 (R-DB): pending. Peer Member 2 (R-ENGINE): pending. Primary live docs and hashes: `../evidence/SOURCES.md`.
