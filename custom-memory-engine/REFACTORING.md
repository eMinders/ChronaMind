# Custom Memory Engine — Refactoring Plan & Handoff

**Status:** restructure complete and verified. Read this file first; it is the
single handoff for the next engineer. Keep it synchronized with the code.

Last verified (all commands run from `custom-memory-engine/`):

- `pytest -q` with the container up: **1305 passed, 21 skipped**
- `pytest -q` with no DB reachable: **1189 passed, 137 skipped** (CI parity)
- `ruff check` / `ruff format --check` on `src tests research/benchmarks evaluation`: clean
- `pyright`: **0 errors, 0 warnings**
- `validation/bitemporal_checks.sql`: **ALL ASSERTIONS PASSED** (43)
- `md5sum -c validation/document_hashes.txt`: OK; `mechanical_checks.py`: ALL HARD
  CHECKS PASSED; `cp_bounds.py` diff: identical
- benchmarks in `reports/benchmarks/`

---

## 1. Target architecture

One installable package. No second import root, no parallel `implementation/` tree.

```text
src/custom_memory_engine/
├── __init__.py
├── config.py            # TestDatabaseConfig: redacting disposable-DB guard
├── protocols.py         # P1 replay scheduling, agreement/κ, zero-leak bound, judge gate
├── pilot_rules.py       # D-09..D-14, D-21, D-22 freeze functions
├── clock/               # R3 monotonic allocator (durable watermark, replay clock)
├── schema/
│   ├── 001_full_schema.sql   # 12 tables, invariants 1-9, RLS, retrieval indexes
│   ├── bitemporal_on_full.sql# 43 assertions replayed on the real table
│   ├── init_extensions.sql   # vector + btree_gist bootstrap
│   ├── migrate.py            # apply / status / confirmed reset, checksum-bound
│   └── README.md
├── engine/
│   ├── predicates.py    # 4 temporal modes, composable SQL, async executor
│   ├── transactions.py  # R1-R5/R7 plus A1/A2/A2b ablations, archival (S)∨(C)
│   ├── retrieval.py     # dense + lexical + RRF(60), A5/A6 channel ablations
│   ├── render.py        # token-budgeted, rank-order-preserving context renderer
│   ├── gate.py          # 5-check write gate, pure decisions + one audit write
│   ├── deletion.py      # R6 lineage closure, re-derivation, epoch, audit
│   └── README.md
└── queue/               # SKIP LOCKED worker, tenant scope, bounded retries

tests/                   # repo-level contracts + pure engine tests
tests/integration/       # PostgreSQL-backed tests (marker: integration)
evaluation/              # harness, baselines B0-B4, judge, stats
research/benchmarks/     # LongMemEval, LoCoMo, own suites T/G/D/I
```

Separation rule: `engine/` holds no provider calls and no tokenizer. Anything
model-backed is injected (gate relation classifier, render token counter), which
is what keeps P and its ablations replayable from one frozen extraction
(`RL-032`).

---

## 2. Completed in this refactoring

### 2.1 Structural

- **Merged two import roots into one package.** `implementation/` (engine core,
  7.3k LOC, *not* packaged by `pyproject.toml`) moved to
  `src/custom_memory_engine/{engine,schema,clock,queue}/` with `git mv` so history
  is preserved. The wheel previously shipped only the 482-LOC contracts module;
  it now ships the engine.
- **Tests consolidated.** `implementation/tests/` split into `tests/` (pure) and
  `tests/integration/` (DB). Added `tests/__init__.py` and
  `tests/integration/__init__.py` so cross-module fixture reuse resolves as
  `tests.integration.*` instead of relying on `pythonpath = ["."]`.
- **Deleted the duplicate decision memos.** `PLAN/decisions/` mirrored root
  `decisions/`; 3 of 6 files were byte-identical and 3 differed only in link
  depth. Root `decisions/` is canonical (`PLAN/DECISIONS_REGISTER.md` and
  `src/custom_memory_engine/decisions`-style links target it).
- **Removed the redundant boundary test.** `implementation/tests/test_independence_boundary.py`
  duplicated `tests/test_boundaries.py` with a weaker AST check (no dynamic
  `importlib`/`__import__` detection). One guard remains.
- **`pyproject.toml`**: dropped `implementation/tests` from `testpaths` and
  `implementation` from pyright `include`.

### 2.2 New engine capability (previously missing; required by the plan)

| Module | Implements | Plan item |
|---|---|---|
| `engine/retrieval.py` | dense cosine + `ts_rank_cd` over `content_fts`, RRF `k=60`, `dense`/`lexical` ablations, deterministic tie-break | `RL-050` (`A5`,`A6`) |
| `engine/render.py` | never renders superseded/archived rows, drops ended validity in `current` mode, rank-order-preserving budget, role labels | `RL-051` |
| `engine/gate.py` | 5 checks in order, `plans_to` routing, injection screen, `A3`/`A4` ablation switches, exact-duplicate detector, `MEMORY_CONFLICT` writer | `RL-060` |
| `engine/deletion.py` | R6 closure, bounded lineage walk, multi-source re-derivation, job purge, epoch, content-free audit | `RL-070` (partial) |

### 2.3 Schema

- Added retrieval indexes to `001_full_schema.sql` (the DDL had **zero**
  `CREATE INDEX`): GiST `(user_id, valid_range, recorded_range)` partial on
  believed statuses, `(user_id, memory_key)`, open-belief partial index,
  `memory_derivation (source_version_id, user_id)`, `memory_embedding
  (embedding_model, version_id)`.
- Added generated column `memory_version.content_fts tsvector GENERATED ALWAYS AS
  (to_tsvector('english', content)) STORED` + GIN index. Measured 3× faster than
  the expression index with identical ranking (see §4).
- Indexes were folded into migration `1` rather than adding migration `2`,
  because `migrate.py` is checksum-bound to a single version and no production
  database exists yet. Do not add a second SQL file without extending the runner.

### 2.4 Documentation

- All engineering/planning docs rewritten from `implementation/...` to
  `src/custom_memory_engine/...` (README, PROJECT_ROADMAP, TRACEABILITY_MATRIX,
  PLAN/*, issue drafts, module READMEs). `src/custom_memory_engine/{schema,engine}`
  READMEs describe the code that now exists.
- CLI path is now `uv run python -m custom_memory_engine.schema.migrate`.
- `evidence/*-verification.md` and `CHANGELOG.md` were **deliberately not**
  rewritten: they are historical records of runs at earlier commits.

---

## 3. Engineering decisions and why

1. **Single package, no compatibility shim.** A `implementation/` re-export
   module was rejected: it would preserve a second import root and let new code
   keep importing the dead path.
2. **`engine/` stays provider-free.** Extraction, embeddings, and answer LLMs
   are injected. This is what allows `P` and ablations to share one frozen
   extraction and keeps the engine testable without network access.
3. **Rank on IDs, project top-k only.** A full version projection costs
   ~25 µs/row; projecting 2000 candidates dominated latency. Ranking now returns
   only ids/scores, and payloads are fetched for the survivors.
4. **Exact cosine, no ANN.** `D-02` keeps HNSW out of the primary configuration;
   ANN is a scalability study (`RL-093`). Do not add HNSW to `P`.
5. **Rejected a bounded lexical candidate pool.** Pre-ranking a `LIMIT`ed pool
   then ranking is 13× faster (1.45 ms vs 19 ms) but recovered only 7/50 of the
   exact top-50. Rejected as a correctness regression.
6. **Single-write gate.** `gate.evaluate` is pure; `gate.record_decision` is the
   only DB effect, so dry runs and ablations need no database.
7. **Deletion re-derives instead of deleting survivors.** A dependent with an
   independent surviving source keeps its row and gets a `re_derivation` edge;
   only dependents whose sources are all doomed are removed.

---

## 4. Measured results

Environment: disposable PostgreSQL 16.15 + pgvector 0.8.7, single connection,
`scripts/bench_retrieval.py`, `limit=50`, P50/P95 over 30 iterations. Raw JSON in
`reports/benchmarks/retrieval_{500,2000}.json`. These are single-user candidate
sets (worst case for the temporal filter), not a multi-tenant load test.

| Candidates | Channel | P50 (ms) | P95 (ms) |
|---:|---|---:|---:|
| 500 | dense | 10.52 | 12.00 |
| 500 | lexical | 4.17 | 4.68 |
| 500 | fused (RRF) | 10.59 | 12.00 |
| 2000 | dense | 26.99 | 29.41 |
| 2000 | lexical | 11.11 | 11.95 |
| 2000 | fused (RRF) | 28.16 | 29.84 |

Change log of the optimizations, each measured:

| Change | Before | After |
|---|---:|---:|
| `to_jsonb(m)` → explicit projection, then → rank-then-project | ~25 µs/row | top-k only |
| Dense: separate id scan + `ANY(2000 ids)` → one SQL join | 40–45 ms @2000 | 27 ms @2000 |
| Lexical: score all matches client-side → `ORDER BY … LIMIT` in SQL | 19 ms @2000 | 11.6 ms @2000 |
| `to_tsvector(content)` per row → generated `content_fts` | 6.44 ms | 2.04 ms |
| Dense candidate count: `count(*) OVER ()` → separate `count(*)` | 6.14 ms | 3.91 ms |
| Reused `ClockTick` re-validated per transaction | unvalidated | checked, 0 tests lost |

**Planner note (verified, not assumed).** The temporal predicate uses
`memory_version_temporal_idx` in both benchmark runs (`Index Scan`, 0.20 ms at
500 rows and 0.78 ms at 2000 rows) and `Bitmap Heap Scan` at tenant densities
such as 50 users × 40 rows (0.02 ms). The plans are deliberately *not* pinned:
when a single tenant holds nearly every row in the table, PostgreSQL chooses a
sequential scan, which is correct for that shape. Do not add index hints to
force a plan.

---

## 5. Tests

Run: `uv run --env-file .env python -m pytest -q` (needs the container from
`compose.yaml`; without it, DB tests skip).

New coverage added in this refactoring:

- `tests/test_retrieval_render.py` (12) — RRF formula, tie-break, A5/A6 channel
  ablations, invalid constants; render drops superseded/archived, omits ended
  validity in `current` but keeps it in an as-of mode with a `previous` label,
  stops at budget, never exceeds budget for one oversized item.
- `tests/test_gate.py` (16) — evidence requires user-authored turns, every
  assertion-mode outcome, injection screen, all conflict relations, confidence
  only under calibration, `A3`/`A4` ablations, check ordering.
- `tests/integration/test_retrieval_deletion_db.py` (11) — dense/lexical/fused
  ranking on real rows including superseded exclusion, audit-query rejection,
  tenant mismatch, render integration, duplicate detection + `MEMORY_CONFLICT`
  write, key deletion, multi-source re-derivation, monotonic epoch, and
  "deleted content is not retrievable".

Verified: `1281 passed, 21 skipped` (integration included, DB up).

---

## 6. Remaining work, in dependency order

1. **`MemorySystemAdapter` for `P` is done.** `evaluation/baselines/p_engine.py`
   implements the seven-method harness protocol over the real schema, with a
   private event loop for async↔sync bridging. The extraction callable is still
   injected. Integration tests in `tests/integration/test_engine_adapter_db.py`
   verify ingest, supersession, retrieval, deletion, the token budget, and
   `A2b` governance. **Next: wire a real frozen extractor and run the harness
   with `--system P`.**
2. **`RL-061` consolidation** — depends on the gate. Required for archival branch
   `(C)` coverage beyond the manual path.
3. **`RL-071` runtime role + grants** — RLS policies exist, but no `app_role`
   with `FORCE ROW LEVEL SECURITY` grants. Suite `I` needs it.
4. **`RL-070` remainder** — the 7-channel leak scanner (caches, prompts, answers,
   logs) belongs to the harness, not `engine/deletion.py`. Deletion scope `topic`
   now matches `subject_key` as well as `memory_key`.
5. **`RL-072`** audit/log redaction guard.
6. **`RL-090`** `T1`–`T5` archival regression harness; **`RL-093`** 8-worker load +
   HNSW-vs-exact study (the numbers in §4 are not that study).
7. **`RL-100`** pilot freeze, then `RL-101`–`RL-105` confirmatory runs.

### Known limitations / open questions

- **Extraction is still injected.** `engine/ingest.py` accepts an extraction
  result as a `Sequence[CandidateVersionInput]`. Calling a model to produce that
  sequence is the caller's job; the engine never loads a model, so `P` and its
  ablations share one frozen extraction (RL-032). The `EngineAdapter` in
  `evaluation/baselines/p_engine.py` accepts an optional `Extractor` callable.
- **`engine/transactions.py` is ~2025 LOC.** All 12 version writes now go
  through one `_insert_version()` helper, so the 18-column list exists once
  (verified by the DB suite). The module still holds six coherent procedures in
  one file; splitting it is optional and not required for correctness.
- **`ensure_archival_trigger` is now a verification helper, not DDL.** The
  `(S)∨(C)` triggers moved into `001_full_schema.sql` in this work, so a freshly
  migrated database is protected without any application call. The Python function
  checks that the triggers and their functions are installed and raises
  `archival_guards_not_installed` if they are absent.
- **`_resolve_clock` now validates a supplied tick** against the transaction's
  own allocation: `app.clock` must already be set to exactly that instant, so a
  tick borrowed from another transaction is rejected
  (`clock_tick_requires_transaction_allocation` / `clock_tick_differs_from_transaction_allocation`).
  A watermark re-derivation was tried first and rejected: rows written earlier in
  the same transaction legitimately share the instant.
- **Lexical relevance is `ts_rank_cd` only** — no BM25 (`D-06` is OFF). Do not
  add it to `P`.
- **Deletion `scope="topic"`** matches every version whose `memory_key` **or**
  `subject_key` equals the target, which is the closest analogue of a topic in
  this data model.
- **Benchmarks are single-connection**, so they measure query + client handling,
  not throughput. Throughput needs `RL-093`.

---

## 7. Conventions to preserve

- Imports at module top; no function-local imports (existing ones in tests are
  fixture helpers and pre-date this rule).
- structlog events are lowercase_underscore with kwargs, never f-strings.
- `psycopg` only: bind values, never interpolate SQL. Schema names are validated
  against `cme_[a-z][a-z0-9_]*` and quoted with `sql.Identifier`.
- Validation errors raise `psycopg.errors.InvalidParameterValue` subclasses with a
  static `.code` and SQLSTATE `22023`, and never echo input values.
- Every engine call requires a caller-owned transaction and a matching
  `app.user_id`; helper functions never commit, close, or change tenant.
- Only `SQLSTATE 40001/40P01` are retried; `23P01` exclusion conflicts are
  surfaced as structured dead letters, never blindly retried.
