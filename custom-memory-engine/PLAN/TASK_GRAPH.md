# Task Graph (`RL-001` – `RL-132`)

**Single Source of Truth for Task Definitions:** Every Research Lab task (`RL-001`–`RL-132`) across Phases `P0`–`P13` is specified below.  
**Visual Diagrams:** See [`DEPENDENCY_MAP.md`](DEPENDENCY_MAP.md).  
**Issue Drafts:** Individual GitHub issue bodies live in [`issue_drafts/`](issue_drafts/).

---

## Phase P0 — Repository, Environment, & CI (`D-P0`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-001` | Approve team plan & Hybrid architecture (`D-ARCH`) | Member 1 (`R-LEAD`) | S | `Done` | — | `RL-002`–`RL-006`, `P1` | `PLAN/ARCHITECTURE.md`, `PLAN/PROJECT_STATUS.md` | Owner sign-off recorded in `PROJECT_STATUS.md`. |
| `RL-002` | Verify `research-lab/` directory scaffold & `.gitignore` | Member 1 (`R-LEAD`) | XS | `Ready` | `RL-001` | `RL-003`–`RL-006` | `research-lab/.gitignore`, verified package directories (`implementation/`, `evaluation/`, `application/`, `reports/`, `research/benchmarks/`) | Layout matches `README.md` §2; transient artifacts (`runs/`, `.venv/`, `__pycache__/`) ignored; `mechanical_checks.py` passes. |
| `RL-003` | Author `research-lab/pyproject.toml`, `uv.lock`, & `.env.example` | Member 2 (`R-ENGINE`) | S | `Blocked` | `RL-002`, `RL-013` | `RL-005`, `RL-006`, `RL-030`, `RL-041` | `pyproject.toml`, `uv.lock`, `.env.example` (`RL_*` vars) | `uv sync` succeeds inside `research-lab/`; zero imports from root `app/`; `pytest --collect-only` exits `0`. |
| `RL-004` | Author `research-lab/docker-compose.yml` (`pgvector` + `btree_gist`) | Member 4 (`R-DB`) | S | `Blocked` | `RL-002`, `RL-012` | `RL-005`, `RL-040`, `RL-041` | `docker-compose.yml`, `implementation/schema/init_extensions.sql` | Container boots on port `5433` with pinned digest (`D-25`); `validation/bitemporal_checks.sql` passes (`43/43` assertions). |
| `RL-005` | Author `.github/workflows/research-lab.yaml` CI workflow | Member 5 (`R-APP`) | S | `Blocked` | `RL-003`, `RL-004` | `P2`–`P13` PR checks | `.github/workflows/research-lab.yaml` | Runs `mechanical_checks.py`, `cp_bounds.py` diff, `bitemporal_checks.sql` against service container, `ruff`, `pyright`, and `pytest`. |
| `RL-006` | Configure `ruff`, `pyright`, & cross-import guard | Member 5 (`R-APP`) | XS | `Blocked` | `RL-003` | `RL-005` | Tool sections in `pyproject.toml`, `implementation/tests/test_independence_boundary.py` | `uv run ruff check .`, `uv run pyright`, and import-boundary test pass with zero warnings. |

---

## Phase P1 — Resolve Open Decisions & Pilot Protocol (`D-P1`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-010` | Prepare research-scope & supervisor decision records (`D-01`–`D-08`, `D-27`) | Member 1 (`R-LEAD`) | M | `Open (#1)` | `RL-001` | `RL-011`, `RL-030`–`RL-033`, `RL-040`, `RL-080`–`RL-083`, `RL-090`, `RL-100`, `RL-122` | `PLAN/decisions/RL-010-scope-and-models.md`, updated `PLAN/DECISIONS_REGISTER.md` & `TECHNICAL_SPECIFICATION.md` §2 | Records frozen or supervisor-ready options for `D-01`–`D-08` and `D-27` with explicit impact on downstream tasks. |
| `RL-011` | Author pre-registered pilot protocol (`evaluation/pilot_plan.md`) | Member 3 (`R-METHOD`) | M | `Blocked` | `RL-010`, `RL-014` | `RL-100` | `evaluation/pilot_plan.md` | Specifies exact dev-split formulas and freeze rules for `D-09` (`B_mem`), `D-10` (`k`), `D-11` (`δ_pres`), `D-12` (`δ_luar`), `D-13` (`u*`, `A8` share), `D-14` (suite sizes), `D-21` (`B4`), and `D-22` (`LongMemEval_S` subset). |
| `RL-012` | Verify PostgreSQL & `pgvector` compatibility evidence (`D-18`, `D-25`) | Member 4 (`R-DB`) | S | `Open (#2)` | `RL-001` | `RL-004`, `RL-040`, `RL-093` | `PLAN/decisions/RL-012-postgres-pgvector.md`, updated `PLAN/DECISIONS_REGISTER.md` | Pins exact PostgreSQL `16.x` and `pgvector` version/digest (`D-25`); documents `btree_gist` + `pg_advisory_xact_lock` concurrency policy and `SERIALIZABLE` fallback (`D-18`). |
| `RL-013` | Select & verify engine runtime and PDT stack (`D-17`, `D-20`) | Member 2 (`R-ENGINE`) | S | `Open (#3)` | `RL-001` | `RL-003`, `RL-030`, `RL-040`, `RL-120`, `RL-121` | `PLAN/decisions/RL-013-engine-runtime.md`, updated `PLAN/DECISIONS_REGISTER.md` | Confirms Python `3.12` + `psycopg` v3 + `pgvector` for the engine (`D-17`) and minimal React/TS or lightweight HTML fallback for the `P12` PDT inspector (`D-20`). |
| `RL-014` | Prepare `A2b` & Suite `D` cardinality decisions (`D-15`, `D-16`) | Member 3 (`R-METHOD`) | S | `Open (#4)` | `RL-001` | `RL-011`, `RL-023`, `RL-024`, `RL-101`, `RL-105` | `PLAN/decisions/RL-014-a2b-and-suite-d.md`, updated `PLAN/DECISIONS_REGISTER.md` | Sets exploratory `A2b` disposition (`D-15`) and selects Suite `D` scenario count `N_D` (`D-16`) backed by `validation/cp_bounds.output.txt`. |
| `RL-015` | Determine ethics requirements for optional usability study (`D-19`) | Member 5 (`R-APP`) | XS | `Open (#5)` | `RL-001` | `RL-122` | `PLAN/decisions/RL-015-ethics-h5.md`, updated `PLAN/DECISIONS_REGISTER.md` | Records university ethics / supervisor determination for `H5` (`D-19`), or marks `H5` `NOT APPLICABLE` if `D-08 = OFF`. |
| `RL-016` | Prepare paired `D-23` / `D-24` & suite-release decisions (`D-26`) | Member 3 (`R-METHOD`) & Member 4 (`R-DB`) | S | `Open (#6)` | `RL-001` | `RL-020`, `RL-022`–`RL-025`, `RL-033`, `RL-132` | `PLAN/decisions/RL-016-paired-d23-d24-d26.md`, updated `PLAN/DECISIONS_REGISTER.md` | Resolves `LongMemEval` timestamp anomaly rule (`D-23`), judge validation `κ` protocol (`D-24`), and own-suite release license (`D-26`). |

---

## Phase P2 — Benchmarks & Own Suites (`D-P2`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-020` | Acquire & verify `LongMemEval` (`oracle`, `S`, `M`) | Member 4 (`R-DB`) | S | `Blocked` | `RL-016` (`D-23`) | `RL-025`, `RL-026` | `research/benchmarks/longmemeval/{fetch.py,MANIFEST.txt,README.md}` | Verifies MIT license, SHA-256 hashes, 500 questions, 30 abstentions, 56 assistant-evidence items, 34 out-of-order session histories, and 43 `question_date < last_turn_ts` items. |
| `RL-021` | Acquire & verify `LoCoMo` (`locomo10.json`) | Member 5 (`R-APP`) | XS | `Ready` | `RL-002` | `RL-025`, `RL-026` | `research/benchmarks/locomo/{fetch.py,MANIFEST.txt,README.md}` | Verifies CC BY-NC 4.0 license, SHA-256 hash, 10 conversations, 1,986 questions, and category 1–5 counts (`282, 321, 96, 841, 446`). |
| `RL-022` | Own Suite `G` (write-governance) & Suite `I` (isolation) generators | Member 3 (`R-METHOD`) | M | `Blocked` | `RL-010` (`D-01`), `RL-016` | `RL-026`, `RL-071`, `RL-103` | `research/benchmarks/own_G/{generator.py,MANIFEST.txt,README.md}`, `research/benchmarks/own_I/{generator.py,MANIFEST.txt,README.md}` | Deterministic seeded script-first generation; held-out injection attack families in test split; `accept-all` and `reject-all` bounds verified. |
| `RL-023` | Own Suite `D` (deletion & canary) generator | Member 3 (`R-METHOD`) | M | `Blocked` | `RL-014` (`D-16`), `RL-016` | `RL-026`, `RL-105` | `research/benchmarks/own_D/{generator.py,MANIFEST.txt,README.md}` | Generates `N_D` scenarios with fictional canaries across direct, consolidated, summary, superseded, and multi-turn re-learning cases, plus never-stated control probes and `A8` positive-control hooks. |
| `RL-024` | Own Suite `T` (temporal-change) generator | Member 3 (`R-METHOD`) | L | `Blocked` | `RL-014` (`D-15`), `RL-016` | `RL-026`, `RL-100`, `RL-101` | `research/benchmarks/own_T/{generator.py,MANIFEST.txt,README.md}` | Script-first scenarios covering updates, corrections, delayed reports, and uncertain start times (`E1`–`E3` patterns); mechanical assignment to `history-dependent` and `present-state` strata. |
| `RL-025` | Benchmark adapters (`LongMemEval`, `LoCoMo`) | Member 4 (`R-DB`) | M | `Blocked` | `RL-020`, `RL-021`, `RL-030` | `RL-026`, `RL-102` | `research/benchmarks/longmemeval/adapter.py`, `research/benchmarks/locomo/adapter.py`, adapter tests | Invokes official scorers without mutating data; chronological session sorting and `ρ = max(question_date, last_turn_ts)` rule (`D-23`) verified. |
| `RL-026` | Freeze development vs. test splits for all suites (`J5` Gate) | Member 4 (`R-DB`) | S | `Blocked` | `RL-022`, `RL-023`, `RL-024`, `RL-025` | `RL-100`, `RL-101`–`RL-105` | `research/benchmarks/splits/{longmemeval,locomo,own_T,own_D,own_G,own_I}.yaml`, `splits/FREEZE_LIST.md` | Deterministic hash split; zero overlap between dev and test scenarios; signed and dated `FREEZE_LIST.md`. |

---

## Phase P3 — Harness, Baselines, Judge, & Statistics (`D-P3`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-030` | Evaluation harness skeleton, `MemorySystemAdapter`, & manifest writer | Member 5 (`R-APP`) | M | `Blocked` | `RL-003`, `RL-013` | `RL-025`, `RL-031`–`RL-034`, `RL-100` | `evaluation/harness/`, `evaluation/stats/__init__.py` | Implements `MemorySystemAdapter` protocol (`TECHNICAL_SPECIFICATION.md` §9.1); repeat runs on dummy baseline produce byte-identical `scores.jsonl` and complete `manifest.json`. |
| `RL-031` | Baseline adapters (`B0`, `B1`, `B2`, `B2h`, `B3` Mem0, conditional `B4` Graphiti) | Member 2 (`R-ENGINE`) | L | `Complete (#23)` | `RL-010` (`D-01`, `D-02`), `RL-030` | `RL-100`, `RL-102` | `evaluation/baselines/{b0_no_memory,b1_full_history,b2_dense,b2h_hybrid,b3_mem0,b4_graphiti}.py` | All baselines share frozen LLM, embedder, and token budget `B_mem`; `B3` pins exact `mem0ai` version; `B4` reports pilot feasibility/cost metrics for `D-21`. |
| `RL-032` | Extraction record-and-replay cache (`Proposal §9`) | Member 2 (`R-ENGINE`) | S | `Blocked` | `RL-030` | `RL-100`, `RL-101`–`RL-105` | `evaluation/harness/replay_cache.py`, unit tests | Caches slow-path extraction outputs keyed by `(turn_id, extractor_version, model_id)`; ablations `P`, `A1`–`A10` replay identical extractions with zero redundant LLM calls. |
| `RL-033` | Answer scoring & LLM judge validation (`Proposal §10.5`) | Member 3 (`R-METHOD`) | M | `In Progress (#30)` | `RL-010` (`D-03`), `RL-016` (`D-24`), `RL-030` | `RL-100`, `RL-101`–`RL-105` | `evaluation/judge/{deterministic.py,llm_judge.py,validation_report.md}` | Deterministic matcher for scripted values; single-answer randomized LLM judge with length-normalization rubric and perturbation check; human `Cohen's κ` validation report on dev sample. |
| `RL-034` | Statistical inference module (`Proposal §10.6`) | Member 3 (`R-METHOD`) | M | `In Progress (#31)` | `RL-030` | `RL-100`, `RL-101`–`RL-105`, `RL-110` | `evaluation/stats/{bootstrap.py,permutation.py,multiplicity.py,clopper_pearson.py}`, `evaluation/stats/tests/` | Implements cluster bootstrap (`B = 10,000`), paired permutation test, Holm–Bonferroni (`H2`), intersection-union rules (`H1`, `H3`), and exact Clopper–Pearson bounds matching `validation/cp_bounds.output.txt`. |

---

## Phase P4 — Schema & Engine Core (`D-P4`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-040` | Full 12-table PostgreSQL DDL (`Proposal §8`, `TECHNICAL_SPECIFICATION.md` §3) | Member 4 (`R-DB`) | L | `In Review (#25)` | `RL-004`, `RL-010`, `RL-012`, `RL-013` | `RL-041`–`RL-044`, `RL-050`, `RL-060`, `RL-061`, `RL-070`, `RL-071`, `RL-090` | `implementation/schema/*.sql`, `implementation/schema/README.md` (invariants 1–10 enforcement table) | Creates all 12 tables (`APP_USER` through `JOB`), `CHECK` constraints, `btree_gist` exclusion constraint (`Inv. 4`), deferred traceability trigger (`Inv. 1`), `guard_update` trigger (`Inv. 3`), and passes `validation/bitemporal_checks.sql`. |
| `RL-041` | Schema migration runner & clean-reset utility | Member 4 (`R-DB`) | S | `Complete (#32)` | `RL-003`, `RL-040` | `RL-042`–`RL-044`, `RL-090`–`RL-093` | `implementation/schema/migrate.py`, `implementation/tests/test_migrations.py`, `evidence/RL-041-verification.md` | Idempotent apply, status/version inspection and confirmed atomic reset against `RL_DATABASE_URL`; clean reset passes all 43 adapted assertions against the full schema. |
| `RL-042` | Transactional supersession (`R1`–`R4`, `R7`) & archival (`R5`, `Inv. 10`) procedures | Member 2 (`R-ENGINE`) | L | `In Progress (#33)` | `RL-040`, `RL-041` | `RL-060`, `RL-061`, `RL-070`, `RL-090`, `RL-091` | `implementation/engine/transactions.py`, SQL procedures | Implements close-then-insert supersession (`R1`), truncated re-issue (`R2`), `conflict_pending` promotion (`R4`), multi-valued end (`R7`), and `archive_version` enforcing `(S) ∨ (C)` (`Inv. 10`) with `A1`/`A2`/`A2b` ablation switches. |
| `RL-043` | Temporal query predicates (`current_state`, `historical_as_of`, `historical_window`, `audit_as_of`) | Member 4 (`R-DB`) | M | `Implemented; review pending (#34)` | `RL-040`, `RL-041` | `RL-050`, `RL-091` | `implementation/engine/predicates.py`, composable SQL, `implementation/tests/test_predicates*.py`, `evidence/RL-043-verification.md` | Matches `TECHNICAL_SPECIFICATION.md` §3.5 and `validation/bitemporal_checks.sql`, including `recorded_range @> ρ`; enforces `t ≤ ρ`, `start < end ≤ ρ` and `β ≤ clock_now()` (`R8`); excludes `Archived` from ordinary retrieval and includes `Archived` in `audit_as_of`. |
| `RL-044` | Injectable monotonic clock (`R3`) & `SKIP LOCKED` slow-path worker queue (`Inv. 9`) | Member 2 (`R-ENGINE`) | M | `Implemented; review pending (#35)` | `RL-040`, `RL-041` | `RL-060`, `RL-092`, `RL-093` | `implementation/clock/`, `implementation/queue/`, `implementation/tests/test_clock*.py`, `implementation/tests/test_queue*.py`, `evidence/RL-044-verification.md` | Enforces durable strictly increasing per-user clock (`+1 µs` on ties/out-of-order input), native SKIP LOCKED claims, D-18 advisory serialization, whole-transaction Tenacity retries, dead-letter routing and atomic job effects/completion. |

---

## Phase P5 — Retrieval, Fusion, & Rendering (`D-P5`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-050` | Hybrid retrieval (`pgvector` dense + PostgreSQL FTS lexical + RRF `k = 60`) | Member 4 (`R-DB`) | M | `Blocked` | `RL-010` (`D-02`), `RL-040`, `RL-043` | `RL-051`, `RL-060`, `RL-093`, `RL-100`, `RL-102` | `implementation/retrieval/{dense.py,lexical.py,fusion.py}` | Combines temporal hard filter (§3.5) with exact per-user cosine similarity and `ts_rank_cd` via RRF (`k = 60`); supports `A5` (`P-dense`), `A6` (`P-lexical`), `A7` (weighted sum), and optional HNSW scalability index. |
| `RL-051` | Query analyzer & token-budgeted context renderer (`Proposal §7.3`) | Member 2 (`R-ENGINE`) | M | `Blocked` | `RL-050` | `RL-100`, `RL-101` | `implementation/retrieval/{query_analyzer.py,renderer.py}` | Classifies query mode (`current`, `historical`, `change`) and target time `t`; enforces the `§7.3` render rule (never render superseded rows; omit historical rows in `current` mode); formats validity intervals within `B_mem`. |

---

## Phase P6 — Validation Gate & Consolidation (`D-P6`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-060` | 5-check write validation gate & conflict detector (`Proposal §7.5`) | Member 2 (`R-ENGINE`) | L | `Blocked` | `RL-042`, `RL-044`, `RL-050` | `RL-091`, `RL-100`, `RL-103` | `implementation/gate/{validator.py,conflict_detector.py,calibration.py}` | Implements checks 1–5 (`TECHNICAL_SPECIFICATION.md` §5), logs all decisions to `MEMORY_CONFLICT`, supports `A3` (`P-nogate`) and `A4` (sub-check) ablations, and gates numeric confidence on dev-split ECE check. |
| `RL-061` | Minimal episodic → semantic/summary consolidation (`Proposal §7.2`, `Inv. 10 (C)`) | Member 2 (`R-ENGINE`) | M | `Blocked` | `RL-042`, `RL-060` | `RL-070`, `RL-090`, `RL-100`, `RL-105` | `implementation/consolidation/` | Produces consolidated semantic/summary versions with `MEMORY_DERIVATION(derived_version_id = r', source_version_id = r)` links and valid ranges covering `V(r)`, enabling safe archival under branch `(C)`. |

---

## Phase P7 — Deletion & Security (`D-P7`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-070` | Transactional lineage deletion, multi-source re-derivation, & 7-channel scanner (`Proposal §7.7`) | Member 2 (`R-ENGINE`) | L | `Blocked` | `RL-042`, `RL-050`, `RL-061` | `RL-090`, `RL-091`, `RL-100`, `RL-105` | `implementation/deletion/{closure.py,rederive.py,scanner.py}` | Single-transaction deletion removing all versions of `memory_key` (`R6`), transitive `MEMORY_DERIVATION` closure, embeddings, FTS, queue payloads, and caches; synchronous re-derivation of surviving multi-source artifacts; `A8` (`P-nolineage`) positive-control toggle; 7-channel leak scanner. |
| `RL-071` | Row-Level Security (`FORCE ROW LEVEL SECURITY`) & cross-user isolation (`Inv. 7`) | Member 4 (`R-DB`) | M | `Blocked` | `RL-022`, `RL-040` | `RL-091` | `implementation/security/rls.sql`, `implementation/security/scoping.py` | Configures restricted non-superuser `app_role` with `FORCE ROW LEVEL SECURITY` on all user tables; service boundary rejects unscoped queries; Suite `I` cross-user exposure count = `0`. |
| `RL-072` | Content-free `AUDIT_EVENT` & structured log redaction guard (`Inv. 8`) | Member 5 (`R-APP`) | S | `Blocked` | `RL-070` | `RL-090`, `RL-091` | `implementation/security/audit_guard.py`, `implementation/tests/test_log_redaction.py` | Verifies that `AUDIT_EVENT` and application structlog outputs never contain memory content, content hashes, subjects, or predicates (Channel 7 check). |

---

## Phase P8 — Optional Components (Gated by `RL-010`, `D-P8`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-080` | Optional retention-score ranking (`A9`, `D-05`) | Member 3 (`R-METHOD`) | S | `Gated (Skip by default)` | `RL-010` (`D-05`), `RL-050` | — | `implementation/optional/retention_ranking.py` (or `SKIPPED` note) | Implemented only if `D-05` enables `A9`; always off in `P`. |
| `RL-081` | Optional Persona layer (`A10`, `D-04`) | Member 2 (`R-ENGINE`) | M | `Gated (Skip by default)` | `RL-010` (`D-04`), `RL-061` | — | `implementation/optional/persona.py` (or `SKIPPED` note) | Implemented only if `D-04` enables `A10`; always off in `P`. |
| `RL-082` | Optional BM25 lexical channel (`D-06`) | Member 4 (`R-DB`) | S | `Gated (Skip by default)` | `RL-010` (`D-06`), `RL-050` | — | `implementation/optional/bm25.py` (or `SKIPPED` note) | Implemented only if `D-06` enables BM25 fallback. |
| `RL-083` | Optional Redis / ARQ queue adapter (`D-07`) | Member 5 (`R-APP`) | S | `Gated (Skip by default)` | `RL-010` (`D-07`), `RL-044` | — | `implementation/optional/redis_queue.py` (or `SKIPPED` note) | Implemented only if `D-07` and `RL-093` load tests require external queueing. |

---

## Phase P9 — Testing Matrix & Verification Gate (`D-P9`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-090` | `T1`–`T5` archival-precondition regression harness (`Invariant 10`) | Member 4 (`R-DB`) | L | `Blocked` | `RL-010` (`D-27`), `RL-042`, `RL-061`, `RL-070`, `RL-072` | `RL-094` | `implementation/tests/archival/test_t1_to_t5.py`, `implementation/tests/archival/README.md` | Deterministically passes `T1`–`T5` (`TECHNICAL_SPECIFICATION.md` §6); updates `TRACEABILITY_MATRIX.md` §2 Invariant 10 to `COMPLETE`. |
| `RL-091` | Full-schema Invariants `1`–`9` test suite | Member 4 (`R-DB`) | M | `Blocked` | `RL-041`–`RL-043`, `RL-060`, `RL-070`–`RL-072` | `RL-094` | `implementation/tests/invariants/test_invariants_1_to_9.py` | Verifies every enforcement site in `TRACEABILITY_MATRIX.md` §2 on the full 12-table PostgreSQL schema. |
| `RL-092` | Replay clock monotonicity (`R3`, `R8`) & worker queue idempotency (`Inv. 9`) tests | Member 2 (`R-ENGINE`) | M | `Blocked` | `RL-044` | `RL-094` | `implementation/tests/test_clock_and_queue.py` | Verifies out-of-order session sorting, `+1 µs` tie increment, `t > ρ` / `β > clock` rejection, `SKIP LOCKED` concurrency, and zero duplicates on job retry. |
| `RL-093` | 8-worker concurrency load test (`D-18`) & HNSW vs. exact recall benchmark | Member 4 (`R-DB`) | M | `Blocked` | `RL-012`, `RL-044`, `RL-050` | `RL-094` | `implementation/tests/test_concurrency_and_hnsw.py`, `reports/analysis/hnsw_recall.md` | Verifies zero `btree_gist` corruption or unhandled deadlocks under 8 concurrent workers; logs P50/P95 retrieval latency and HNSW recall vs. exact scan. |
| `RL-094` | Full `PROJECT_ROADMAP.md` §G testing matrix review gate (`J7` Gate) | Member 4 (`R-DB`) | M | `Blocked` | `RL-090`–`RL-093` | `RL-100` | `implementation/tests/report.md`, updated `TRACEABILITY_MATRIX.md` §2 & §5 | Every mandatory row in `PROJECT_ROADMAP.md` §G is `COMPLETE` and linked to test evidence; signed off by Member 4 and peer reviewer Member 2. |

---

## Phase P10 — Pilot & Confirmatory Experiments (`D-P10`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-100` | Pilot run on development split & parameter freeze (`J8` Gate) | Member 3 (`R-METHOD`) | L | `Blocked` | `RL-010`, `RL-011`, `RL-026`, `RL-031`–`RL-034`, `RL-094` | `RL-101`–`RL-105` | `evaluation/runs/pilot/FREEZE_LIST.md`, updated `PLAN/DECISIONS_REGISTER.md` (`D-09`–`D-14`, `D-21`, `D-22`) | Executes on **development split only**; freezes `B_mem`, `k`, `δ_pres`, `δ_luar`, `u*`, `A8` share, test-split sizes, `B4` inclusion, and `LongMemEval_S` subset with supervisor sign-off. |
| `RL-101` | `H1` confirmatory experiment on Suite `T` test split (run once) | Member 3 (`R-METHOD`) | M | `Blocked` | `RL-100`, `RL-024`, `RL-051` | `RL-110` | `evaluation/runs/<ts>/H1/{manifest.json,answers.jsonl,scores.jsonl,per_cluster.jsonl,report.md}` | Touches Suite `T` test split exactly once; computes `P − A2` on `history-dependent` and `present-state` strata + `A1` diagnostic. |
| `RL-102` | `H2` confirmatory experiment on `LongMemEval_S` test split & `LoCoMo` descriptive run | Member 4 (`R-DB`) | L | `Blocked` | `RL-100`, `RL-025`, `RL-031`, `RL-050` | `RL-110` | `evaluation/runs/<ts>/H2/{manifest.json,scores.jsonl,per_cluster.jsonl,report.md}`, `LoCoMo` descriptive report | Touches `LongMemEval_S` test split exactly once; computes `Evidence Recall@k` for `P`, `A5`, `A6` with Holm–Bonferroni (`α = 0.05`) + `B0`–`B4` comparisons. |
| `RL-103` | `H3` confirmatory experiment on Suite `G` test split (run once) | Member 2 (`R-ENGINE`) | M | `Blocked` | `RL-100`, `RL-022`, `RL-060` | `RL-110` | `evaluation/runs/<ts>/H3/{manifest.json,scores.jsonl,per_cluster.jsonl,report.md}` | Touches Suite `G` test split exactly once; computes co-primary `ASR(P) − ASR(A3)` and `LUAR(P)` vs. `1 − δ_luar` + `A4` diagnostics. |
| `RL-104` | Dual-annotator human audit (`Cohen's κ`) on Suite `T`/`G` sample & judge verification | Member 5 (`R-APP`) | M | `Blocked` | `RL-022`, `RL-024`, `RL-033` | `RL-110` | `evaluation/judge/human_audit_kappa.md` | Two independent annotators label a stratified sample of Suite `T`/`G` utterances and answers; reports `Cohen's κ` and confusion matrices. |
| `RL-105` | `H4` confirmatory experiment on Suite `D` test split (run once) | Member 2 (`R-ENGINE`) | M | `Blocked` | `RL-100`, `RL-023`, `RL-061`, `RL-070` | `RL-110` | `evaluation/runs/<ts>/H4/{manifest.json,scores.jsonl,per_cluster.jsonl,report.md}` | Touches Suite `D` test split exactly once; evaluates scenario-level `PDLR(P)`, exact 97.5% CP upper bound `≤ u*`, and `A8` positive-control sensitivity across all 7 channels. |

---

## Phase P11 — Reporting & Analysis (`D-P11`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-110` | Confirmatory statistical analysis & tables (`H1`–`H4`) | Member 3 (`R-METHOD`) | M | `Blocked` | `RL-101`–`RL-105` | `RL-130` | `reports/analysis/confirmatory_results.md`, tables & plots | Mechanically applies frozen §4.1 decision rules to `RL-101`–`RL-105` outputs; reports `Supported`, `Not supported`, or `Inconclusive` with 95% cluster-bootstrap CIs. |
| `RL-111` | Efficiency, latency (`P50`/`P95`), visibility lag, & token-cost profiling | Member 4 (`R-DB`) | S | `Blocked` | `RL-093`, `RL-101`–`RL-105` | `RL-130` | `reports/analysis/efficiency_and_cost.md` | Reports retrieval latency, end-to-end latency, slow-path visibility lag, token/USD cost per turn/query, and DB storage growth per 1,000 turns. |

---

## Phase P12 — Personal Digital Twin (PDT) Application (`D-P12`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-120` | Personal Digital Twin (PDT) FastAPI backend (`Proposal §11`) | Member 5 (`R-APP`) | M | `Blocked` | `RL-013`, `RL-051`, `RL-060`, `RL-070`, `RL-071` | `RL-121`, `RL-130` | `application/api/` | Exposes chat turn ingestion, bitemporal timeline inspection (`current`, `historical_as_of`, `audit_as_of`), `conflict_pending` resolution, and deletion endpoints over `implementation/`. |
| `RL-121` | Personal Digital Twin (PDT) Memory Inspector UI (`Proposal §11`) | Member 5 (`R-APP`) | M | `Blocked` | `RL-013` (`D-20`), `RL-120` | `RL-122`, `RL-130` | `application/web/` | Displays bitemporal (`valid_range` × `recorded_range`) version timeline, provenance spans/derivation links, conflict queue controls, and one-click memory/topic deletion. |
| `RL-122` | Optional PDT usability study (`H5`, gated by `D-08` & `RL-015`) | Member 5 (`R-APP`) | S | `Gated (Skip by default)` | `RL-010` (`D-08`), `RL-015` (`D-19`), `RL-121` | `RL-130` | `reports/analysis/usability_h5.md` (or `SKIPPED` note) | Executed only if `D-08` and `D-19` ethics approval are active prior to recruitment; reports descriptive task completion and SUS. |

---

## Phase P13 — Final Packaging & Release (`D-P13`)

| ID | Title | Owner | Effort | Status | Blocked By | Blocks | Deliverables (`research-lab/...`) | Acceptance Criteria |
|---|---|---|---|---|---|---|---|---|
| `RL-130` | Final graduation thesis & verification report (`Deliverable D5`) | Member 1 (`R-LEAD`) | L | `Blocked` | `RL-110`, `RL-111`, `RL-121` | `RL-131` | `reports/thesis/`, updated `TRACEABILITY_MATRIX.md` | Synthesizes `H1`–`H4` outcomes, limitations, and engineering artifacts without overclaiming beyond predeclared decision rules. |
| `RL-131` | One-command reproducibility pack & clean-checkout audit | Member 2 (`R-ENGINE`) | M | `Blocked` | `RL-110`, `RL-130` | Release | `reports/reproducibility/reproduce_all.sh`, `reports/reproducibility/REPRODUCTION_LOG.txt` | Clean checkout + single command reproduces all invariant tests and regenerates `H1`–`H4` statistical tables from saved run artifacts. |
| `RL-132` | Package & release own evaluation suites (`T`, `G`, `D`, `I`) (`Deliverable D3`) | Member 3 (`R-METHOD`) | S | `Blocked` | `RL-016` (`D-26`), `RL-100`, `RL-110` | Release | `research/benchmarks/RELEASE_MANIFEST.md` | Packages validated generators, scripts, and schemas under the approved license (`D-26`) with zero `LoCoMo`-derived text. |
