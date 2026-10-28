# Consolidated Issue Drafts (`RL-031` – `RL-132`)

This file holds the ready-to-publish issue specifications for the remaining tasks in [`../TASK_GRAPH.md`](../TASK_GRAPH.md) that do not have separate individual files in `issue_drafts/`. All tasks follow the workflow and verification rules in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

---

## Phase P3 — Baselines, Replay Cache, Judge, & Statistics

### `[RL-031]` Baseline adapters (`B0`, `B1`, `B2`, `B2h`, `B3` Mem0, conditional `B4` Graphiti)
- **Milestone:** `P3 Harness, baselines, judge, statistics` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 4 (`R-DB`)
- **Blocked By:** `RL-010` (`#1`), `RL-030` · **Blocks:** `RL-100`, `RL-102`
- **Objective & Scope:** Implement `MemorySystemAdapter` wrappers (`evaluation/baselines/`) for `B0` (current session only), `B1` (full history up to context limit, logging truncation count), `B2` (raw-turn dense retrieval), `B2h` (raw-turn dense + FTS + RRF `k = 60`), `B3` (`mem0ai` pinned commit/version configured with frozen LLM/embedder), and `B4` (`graphiti-core` conditional adapter instrumented to measure dev-split ingestion cost and stability for `D-21`).
- **Acceptance Criteria:**
  - [ ] All baselines share the exact answering LLM (`D-01`), embedder (`D-02`), and retrieved token budget `B_mem` (`D-09`).
  - [ ] `mem0ai` is imported only inside `evaluation/baselines/b3_mem0.py` (`PLAN/INDEPENDENCE_BOUNDARY.md` §3.2).
  - [ ] `B4` logs per-episode LLM call count and latency for the `D-21` pilot gate.

---

### `[RL-032]` Extraction record-and-replay cache (`Proposal §9`)
- **Milestone:** `P3 Harness, baselines, judge, statistics` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 5 (`R-APP`)
- **Blocked By:** `RL-030` · **Blocks:** `RL-100`, `RL-101`–`RL-105`
- **Objective & Scope:** Implement `evaluation/harness/replay_cache.py` keying slow-path extraction candidates by `(turn_id, extractor_version, model_id)` so ablations (`P`, `A1`–`A10`) replay identical extracted candidates without re-invoking the LLM (`Graduation_Project_Proposal.md` §9).
- **Acceptance Criteria:**
  - [ ] Cache hit returns byte-identical candidate dicts with zero external LLM calls.
  - [ ] Cache misses only store successful schema-validated extractions (never errors).

---

### `[RL-033]` Answer scoring and LLM judge validation (`Proposal §10.5`)
- **Milestone:** `P3 Harness, baselines, judge, statistics` · **Labels:** `enhancement`
- **Owner:** Member 3 (`R-METHOD`) · **Reviewer:** Member 1 (`R-LEAD`)
- **Blocked By:** `RL-010` (`#1`, `D-03`), `RL-016` (`#6`, `D-24`), `RL-030` · **Blocks:** `RL-100`, `RL-101`–`RL-105`
- **Objective & Scope:** Implement `evaluation/judge/deterministic.py` (exact/normalized match on scripted values) and `evaluation/judge/llm_judge.py` (single-answer randomized presentation, length-normalization rubric, distinct judge model `D-03`, perturbation check with appended irrelevant text), and validate against dual human annotations (`D-24`) on the development sample (`evaluation/judge/validation_report.md`).
- **Acceptance Criteria:**
  - [ ] Reports human-judge `Cohen's κ`, accuracy, confusion matrix, and perturbation stability on the dev sample before any test split is scored.

---

### `[RL-034]` Statistical inference module (`Proposal §10.6`)
- **Milestone:** `P3 Harness, baselines, judge, statistics` · **Labels:** `enhancement`
- **Owner:** Member 3 (`R-METHOD`) · **Reviewer:** Member 1 (`R-LEAD`)
- **Blocked By:** `RL-030` · **Blocks:** `RL-094`, `RL-100`, `RL-101`–`RL-105`, `RL-110`
- **Objective & Scope:** Implement `evaluation/stats/{bootstrap.py,permutation.py,multiplicity.py,clopper_pearson.py}` providing paired non-parametric cluster bootstrap (`B = 10,000`), paired permutation tests, Holm–Bonferroni step-down (`H2` at `α = 0.05`), intersection-union decision evaluators (`H1`, `H3`), and exact one-sided 97.5% Clopper–Pearson upper bounds (`H4`).
- **Acceptance Criteria:**
  - [ ] Clopper–Pearson function reproduces `validation/cp_bounds.output.txt` exactly across `N = 20..100`.
  - [ ] Unit tests verify cluster resampling preserves scenario/question pairing (`STAT-UNIT` row in `PROJECT_ROADMAP.md` §G).

---

## Phase P4 — Schema Migrations, Transactions, Predicates, & Queue

### `[RL-041]` Schema migration runner and clean-reset utility
- **Milestone:** `P4 Schema and engine core` · **Labels:** `enhancement`
- **Owner:** Member 4 (`R-DB`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-003`, `RL-040` · **Blocks:** `RL-042`–`RL-044`, `RL-090`–`RL-093`
- **Deliverables:** `src/custom_memory_engine/schema/migrate.py`, `tests/integration/test_migrations.py`.
- **Acceptance Criteria:** Idempotent apply/reset migrations against `RL_DATABASE_URL`; clean reset passes all 43 assertions of `validation/bitemporal_checks.sql`.

---

### `[RL-042]` Transactional supersession (`R1`–`R4`, `R7`) and archival (`R5`, `Invariant 10`) procedures
- **Milestone:** `P4 Schema and engine core` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 4 (`R-DB`)
- **Blocked By:** `RL-040`, `RL-041` · **Blocks:** `RL-060`, `RL-061`, `RL-070`, `RL-090`, `RL-091`
- **Deliverables:** `src/custom_memory_engine/engine/transactions.py` and SQL procedures.
- **Acceptance Criteria:** Implements close-then-insert (`R1`), truncated re-issue (`R2`), `conflict_pending` promotion (`R4`), multi-valued end (`R7`), `archive_version` enforcing `(S) ∨ (C)` (`TECHNICAL_SPECIFICATION.md` §6), and ablation switches `A1` (`P-flat`), `A2` (`P-nohistory`), and `A2b` (if enabled by `D-15`).

---

### `[RL-043]` Temporal query predicates (`current_state`, `historical_as_of`, `historical_window`, `audit_as_of`)
- **Milestone:** `P4 Schema and engine core` · **Labels:** `enhancement`
- **Owner:** Member 4 (`R-DB`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-040`, `RL-041` · **Blocks:** `RL-050`, `RL-091`
- **Deliverables:** `src/custom_memory_engine/engine/predicates.py` and SQL query functions (`TECHNICAL_SPECIFICATION.md` §3.5).
- **Acceptance Criteria:** Enforces `t ≤ ρ` on `historical_as_of` and `β ≤ clock_now()` on `audit_as_of` (`R8`); excludes `Archived` rows from `current_state`/`historical_as_of` and includes `Archived` rows in `audit_as_of`.

---

### `[RL-044]` Injectable monotonic clock (`R3`) and `SKIP LOCKED` slow-path worker queue (`Invariant 9`)
- **Milestone:** `P4 Schema and engine core` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 4 (`R-DB`)
- **Blocked By:** `RL-040`, `RL-041` · **Blocks:** `RL-060`, `RL-092`, `RL-093`
- **Deliverables:** `src/custom_memory_engine/clock/`, `src/custom_memory_engine/queue/`.
- **Acceptance Criteria:** Monotonic per-user clock (`+1 µs` on ties), `SELECT ... FOR UPDATE SKIP LOCKED` job claiming, per-user `pg_advisory_xact_lock(hashtext(user_id))` serialization, tenacity exponential backoff retry, dead-letter routing, and idempotency (`Invariant 9`).

---

## Phase P5 — Retrieval, Fusion, & Rendering

### `[RL-050]` Hybrid retrieval (`pgvector` dense + PostgreSQL FTS lexical + RRF `k = 60`)
- **Milestone:** `P5 Retrieval, fusion, rendering` · **Labels:** `enhancement`
- **Owner:** Member 4 (`R-DB`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-010` (`#1`, `D-02`), `RL-040`, `RL-043` · **Blocks:** `RL-051`, `RL-060`, `RL-093`, `RL-100`, `RL-102`
- **Deliverables:** `src/custom_memory_engine/retrieval/{dense.py,lexical.py,fusion.py}`.
- **Acceptance Criteria:** Applies mode-specific hard temporal predicate first (`§7.6`), ranks dense (`embedding <=> query_vec`) and lexical (`ts_rank_cd`), fuses with `RRF(m) = Σ 1/(60 + rank_c(m))`, and supports ablation toggles `A5` (`P-dense`), `A6` (`P-lexical`), and `A7` (weighted sum).

---

### `[RL-051]` Query analyzer and token-budgeted context renderer (`Proposal §7.3`)
- **Milestone:** `P5 Retrieval, fusion, rendering` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 3 (`R-METHOD`)
- **Blocked By:** `RL-050` · **Blocks:** `RL-100`, `RL-101`, `RL-120`
- **Deliverables:** `src/custom_memory_engine/retrieval/{query_analyzer.py,renderer.py}`.
- **Acceptance Criteria:** Classifies query mode (`current`, `historical`, `change`) and target time `t`; enforces the `§7.3` render rule (never render superseded versions; never render historical versions in `current` mode); formats role labels (`current`, `historical`, `previous`/`current`) within `B_mem` inside a snapshot-consistent transaction.

---

## Phase P6 — Validation Gate & Consolidation

### `[RL-060]` 5-check write validation gate and conflict detector (`Proposal §7.5`)
- **Milestone:** `P6 Validation gate and consolidation` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 3 (`R-METHOD`)
- **Blocked By:** `RL-042`, `RL-044`, `RL-050` · **Blocks:** `RL-061`, `RL-091`, `RL-100`, `RL-103`, `RL-120`
- **Deliverables:** `src/custom_memory_engine/gate/{validator.py,conflict_detector.py,calibration.py}`.
- **Acceptance Criteria:** Enforces checks 1–5 (`TECHNICAL_SPECIFICATION.md` §5: user evidence span, assertion mode, instruction screen, conflict relation, calibrated confidence check), writes every decision to `MEMORY_CONFLICT`, and supports `A3` (`P-nogate`) and `A4` (sub-check) ablations.

---

### `[RL-061]` Minimal episodic → semantic/summary consolidation (`Proposal §7.2`, `Invariant 10 (C)`)
- **Milestone:** `P6 Validation gate and consolidation` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 4 (`R-DB`)
- **Blocked By:** `RL-042`, `RL-060` · **Blocks:** `RL-070`, `RL-081`, `RL-090`, `RL-100`, `RL-105`
- **Deliverables:** `src/custom_memory_engine/consolidation/`.
- **Acceptance Criteria:** Creates consolidated semantic/summary versions with `MEMORY_DERIVATION(derived_version_id = r', source_version_id = r)` links and valid ranges covering `V(r)` (`V(r') @> V(r)`), satisfying Invariant 1 and enabling safe archival via branch `(C)`.

---

## Phase P7 — Deletion & Security

### `[RL-070]` Transactional lineage deletion, multi-source re-derivation, and 7-channel scanner (`Proposal §7.7`)
- **Milestone:** `P7 Deletion and security` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 4 (`R-DB`)
- **Blocked By:** `RL-042`, `RL-050`, `RL-061` · **Blocks:** `RL-072`, `RL-090`, `RL-091`, `RL-100`, `RL-105`, `RL-120`
- **Deliverables:** `src/custom_memory_engine/deletion/{closure.py,rederive.py,scanner.py}`.
- **Acceptance Criteria:** Single-transaction deletion removing all versions of `memory_key` (`R6`), recursive `MEMORY_DERIVATION` closure (`depth ≤ 3`), embeddings, FTS entries, queue payloads, and cache entries keyed by `deletion_epoch`; synchronous re-derivation of multi-source summaries from surviving `Active` sources; `A8` (`P-nolineage`) toggle; 7-channel verification scanner (`Proposal` §10.3).

---

### `[RL-071]` Row-Level Security (`FORCE ROW LEVEL SECURITY`) and cross-user isolation (`Invariant 7`)
- **Milestone:** `P7 Deletion and security` · **Labels:** `enhancement`
- **Owner:** Member 4 (`R-DB`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-022`, `RL-040` · **Blocks:** `RL-091`, `RL-120`
- **Deliverables:** `src/custom_memory_engine/security/rls.sql`, `src/custom_memory_engine/security/scoping.py`.
- **Acceptance Criteria:** Non-superuser `app_role` without `BYPASSRLS` + `FORCE ROW LEVEL SECURITY` on all user tables; service-boundary guard rejects missing/mismatched `user_id`; zero cross-user leaks on Suite `I`.

---

### `[RL-072]` Content-free `AUDIT_EVENT` and structured log redaction guard (`Invariant 8`)
- **Milestone:** `P7 Deletion and security` · **Labels:** `enhancement`
- **Owner:** Member 5 (`R-APP`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-070` · **Blocks:** `RL-090`, `RL-091`
- **Deliverables:** `src/custom_memory_engine/security/audit_guard.py`, `tests/integration/test_log_redaction.py`.
- **Acceptance Criteria:** Enforces schema validation on `AUDIT_EVENT.details` and structlog processors so memory content, content hashes, subjects, and predicates never appear in audit tables or logs (Channel 7).

---

## Phase P8 — Optional Components (Gated by `RL-010`)

### `[RL-080]`–`[RL-083]` Optional exploratory modules (`A9`, `A10`, BM25, Redis queue)
- **Milestone:** `P8 Optional components` · **Labels:** `enhancement`
- **Blocked By:** `RL-010` (`#1`, `D-04`–`D-07`)
- **Scope:** Implemented **only if** explicitly enabled in `RL-010` (`D-04` Persona `A10` → `RL-081`; `D-05` Retention ranking `A9` → `RL-080`; `D-06` BM25 → `RL-082`; `D-07` Redis queue → `RL-083`). Otherwise closed with a `SKIPPED per D-04..D-07` note in `src/custom_memory_engine/optional/README.md`.

---

## Phase P9 — Testing Matrix (`RL-091`–`RL-093`)

*(Note: `RL-090` and `RL-094` have standalone files `RL-090.md` and `RL-094.md`.)*

### `[RL-091]` Full-schema Invariants `1`–`9` test suite
- **Milestone:** `P9 Testing matrix` · **Labels:** `enhancement`
- **Owner:** Member 4 (`R-DB`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-041`–`RL-043`, `RL-060`, `RL-070`–`RL-072` · **Blocks:** `RL-094`
- **Deliverables:** `tests/integration/invariants/test_invariants_1_to_9.py`.
- **Acceptance Criteria:** Exercises every invariant `1`–`9` (`INV-1`, `INV-2-6`, `INV-7-RLS`, `INV-8-DEL`, `INV-9-IDEM` in `PROJECT_ROADMAP.md` §G) against the full 12-table PostgreSQL schema.

---

### `[RL-092]` Replay clock monotonicity (`R3`, `R8`) and worker queue idempotency (`Invariant 9`) tests
- **Milestone:** `P9 Testing matrix` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 4 (`R-DB`)
- **Blocked By:** `RL-044` · **Blocks:** `RL-094`
- **Deliverables:** `tests/integration/test_clock_and_queue.py`.
- **Acceptance Criteria:** Verifies out-of-order session sorting, `+1 µs` tie increment, `t > ρ` and `β > clock` rejection (`R8`), `SKIP LOCKED` worker claiming, per-user advisory lock ordering, and zero duplicate memories on job retry (`CLK-REP` and `INV-9-IDEM` in `PROJECT_ROADMAP.md` §G).

---

### `[RL-093]` 8-worker concurrency load test (`D-18`) and HNSW vs. exact recall benchmark
- **Milestone:** `P9 Testing matrix` · **Labels:** `enhancement`
- **Owner:** Member 4 (`R-DB`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-012` (`#2`), `RL-044`, `RL-050` · **Blocks:** `RL-094`, `RL-111`
- **Deliverables:** `tests/integration/test_concurrency_and_hnsw.py`, `reports/analysis/hnsw_recall.md`.
- **Acceptance Criteria:** Runs 8 concurrent slow-path workers issuing supersessions (`R1`) across users and against the same user; verifies zero `no_bitemporal_overlap` constraint violations or unhandled deadlocks (`D-18`), and records ANN HNSW recall vs. exact scan (`LOAD-CONC` in `PROJECT_ROADMAP.md` §G).

---

## Phase P10 — Confirmatory Experiments (`RL-101`–`RL-104`)

*(Note: `RL-100` and `RL-105` have standalone files `RL-100.md` and `RL-105.md`.)*

### `[RL-101]` `H1` confirmatory experiment on Suite `T` test split (run once)
- **Milestone:** `P10 Pilot and experiments` · **Labels:** `enhancement`
- **Owner:** Member 3 (`R-METHOD`) · **Reviewer:** Member 1 (`R-LEAD`)
- **Blocked By:** `RL-100` (`Gate J8`), `RL-024`, `RL-051` · **Blocks:** `RL-110`
- **Deliverables:** `evaluation/runs/<ts>/H1/{manifest.json,answers.jsonl,scores.jsonl,per_cluster.jsonl,report.md}`.
- **Acceptance Criteria:** Touches Suite `T` test split exactly once; evaluates `P` vs. `A2` (`P-nohistory`) on both co-primary strata (`history-dependent` superiority `> 0` and `present-state` non-inferiority `≥ −δ_pres`), plus diagnostic `A1` (`P-flat`), `B0`, and deterministic `audit_as_of` accuracy.

---

### `[RL-102]` `H2` confirmatory experiment on `LongMemEval_S` test split and `LoCoMo` descriptive run
- **Milestone:** `P10 Pilot and experiments` · **Labels:** `enhancement`
- **Owner:** Member 4 (`R-DB`) · **Reviewer:** Member 3 (`R-METHOD`)
- **Blocked By:** `RL-100` (`Gate J8`), `RL-025`, `RL-031`, `RL-050` · **Blocks:** `RL-110`, `RL-111`
- **Deliverables:** `evaluation/runs/<ts>/H2/{manifest.json,scores.jsonl,per_cluster.jsonl,report.md}`, `evaluation/runs/<ts>/LoCoMo_descriptive/`.
- **Acceptance Criteria:** Touches `LongMemEval_S` test split (or frozen `D-22` subset) exactly once; evaluates `Evidence Recall@k` (`D-10`) for `P` vs. `A5` (`P-dense`) and `P` vs. `A6` (`P-lexical`) under Holm–Bonferroni (`α = 0.05`), plus `A7`, `B0`–`B3` (and `B4` if `D-21` passed), and descriptive `LoCoMo` results per conversation.

---

### `[RL-103]` `H3` confirmatory experiment on Suite `G` test split (run once)
- **Milestone:** `P10 Pilot and experiments` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 3 (`R-METHOD`)
- **Blocked By:** `RL-100` (`Gate J8`), `RL-022`, `RL-060` · **Blocks:** `RL-110`
- **Deliverables:** `evaluation/runs/<ts>/H3/{manifest.json,scores.jsonl,per_cluster.jsonl,report.md}`.
- **Acceptance Criteria:** Touches Suite `G` test split exactly once; evaluates co-primary intersection-union test (`ASR(P) − ASR(A3) < 0` and `LUAR(P) ≥ 1 − δ_luar`), plus `A4` sub-check ablation and conflict-detector F1 diagnostics.

---

### `[RL-104]` Dual-annotator human audit (`Cohen's κ`) on Suite `T`/`G` sample and judge verification
- **Milestone:** `P10 Pilot and experiments` · **Labels:** `enhancement`
- **Owner:** Member 5 (`R-APP`) · **Reviewer:** Member 1 (`R-LEAD`)
- **Blocked By:** `RL-022`, `RL-024`, `RL-033` · **Blocks:** `RL-110`
- **Deliverables:** `evaluation/judge/human_audit_kappa.md`.
- **Acceptance Criteria:** Blind annotation by Member 5 and Member 1 (`PLAN/ASSIGNMENTS.md` §3) on the stratified sample defined in `D-24`; reports `Cohen's κ`, unresolved disagreement count, and judge agreement.

---

## Phases P11–P13 — Analysis, Personal Digital Twin App, & Release

### `[RL-110]` Confirmatory statistical analysis and tables (`H1`–`H4`)
- **Milestone:** `P11 Reporting and analysis` · **Labels:** `documentation`
- **Owner:** Member 3 (`R-METHOD`) · **Reviewer:** Member 1 (`R-LEAD`)
- **Blocked By:** `RL-101`–`RL-105` · **Blocks:** `RL-130`, `RL-131`, `RL-132`
- **Deliverables:** `reports/analysis/confirmatory_results.md`, tables & figures.
- **Acceptance Criteria:** Applies frozen §4.1 decision rules mechanically; reports point estimates, 95% cluster-bootstrap CIs, p-values, Holm adjustments (`H2`), and exact Clopper–Pearson bounds (`H4`, Suite `I`), classifying each hypothesis as `Supported`, `Not supported`, or `Inconclusive`.

---

### `[RL-111]` Efficiency, latency (`P50`/`P95`), visibility lag, and token-cost profiling
- **Milestone:** `P11 Reporting and analysis` · **Labels:** `documentation`
- **Owner:** Member 4 (`R-DB`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-093`, `RL-101`–`RL-105` · **Blocks:** `RL-130`
- **Deliverables:** `reports/analysis/efficiency_and_cost.md`.
- **Acceptance Criteria:** Reports retrieval latency (`P50`/`P95`), end-to-end latency, slow-path visibility lag (`P50`/`P95`), token/USD cost per turn and query, and DB storage growth per 1,000 turns (`Graduation_Project_Proposal.md` §10.3).

---

### `[RL-120]` Personal Digital Twin (PDT) FastAPI backend (`Proposal §11`)
- **Milestone:** `P12 Personal Digital Twin application` · **Labels:** `enhancement`
- **Owner:** Member 5 (`R-APP`) · **Reviewer:** Member 2 (`R-ENGINE`)
- **Blocked By:** `RL-013` (`#3`), `RL-051`, `RL-060`, `RL-070`, `RL-071` · **Blocks:** `RL-121`, `RL-130`
- **Deliverables:** `application/api/`.
- **Acceptance Criteria:** Thin FastAPI service over `src/custom_memory_engine/` providing chat turn ingestion, bitemporal timeline queries (`current_state`, `historical_as_of`, `audit_as_of`), `conflict_pending` resolution, and deletion requests (`memory`, `topic`, `all`).

---

### `[RL-121]` Personal Digital Twin (PDT) Memory Inspector UI (`Proposal §11`)
- **Milestone:** `P12 Personal Digital Twin application` · **Labels:** `enhancement`
- **Owner:** Member 5 (`R-APP`) · **Reviewer:** Member 1 (`R-LEAD`)
- **Blocked By:** `RL-013` (`#3`, `D-20`), `RL-120` · **Blocks:** `RL-122`, `RL-130`
- **Deliverables:** `application/web/`.
- **Acceptance Criteria:** Renders the Memory Inspector timeline (`valid_range` × `recorded_range`), provenance spans/derivation links, retrieval score explanation panel, conflict resolution controls, and forget/delete controls (including the exclusive-evidence turn deletion option per `Proposal` §7.7).

---

### `[RL-122]` Optional PDT usability study (`H5`, gated by `D-08` and `RL-015`)
- **Milestone:** `P12 Personal Digital Twin application` · **Labels:** `enhancement`
- **Owner:** Member 5 (`R-APP`) · **Reviewer:** Member 1 (`R-LEAD`)
- **Blocked By:** `RL-010` (`#1`, `D-08`), `RL-015` (`#5`, `D-19`), `RL-121` · **Blocks:** `RL-130`
- **Deliverables:** `reports/analysis/usability_h5.md` (or `SKIPPED` note).
- **Acceptance Criteria:** Executed only if both `D-08` and written `D-19` ethics clearance are active before recruitment; otherwise documented as `SKIPPED per D-08/D-19`.

---

### `[RL-130]` Final graduation thesis and verification report (`Deliverable D5`)
- **Milestone:** `P13 Final packaging` · **Labels:** `documentation`
- **Owner:** Member 1 (`R-LEAD`) · **Reviewer:** All team members
- **Blocked By:** `RL-110`, `RL-111`, `RL-121` · **Blocks:** `RL-131`
- **Deliverables:** `reports/thesis/`, final `TRACEABILITY_MATRIX.md`.
- **Acceptance Criteria:** Complete thesis report accurately reflecting `H1`–`H4` outcomes (including any null/negative results per `Proposal` §6.3), limitations, and verified invariants `1`–`10`.

---

### `[RL-131]` One-command reproducibility pack and clean-checkout audit (`Deliverable D5`)
- **Milestone:** `P13 Final packaging` · **Labels:** `enhancement`
- **Owner:** Member 2 (`R-ENGINE`) · **Reviewer:** Member 4 (`R-DB`)
- **Blocked By:** `RL-110`, `RL-130` · **Blocks:** Final release
- **Deliverables:** `reports/reproducibility/reproduce_all.sh`, `reports/reproducibility/REPRODUCTION_LOG.txt`.
- **Acceptance Criteria:** From a clean checkout, `./reports/reproducibility/reproduce_all.sh` runs mechanical checks, SQL/invariant tests, and regenerates all `H1`–`H4` tables/figures from saved run logs with zero discrepancies.

---

### `[RL-132]` Package and release own evaluation suites (`T`, `G`, `D`, `I`) (`Deliverable D3`)
- **Milestone:** `P13 Final packaging` · **Labels:** `enhancement`
- **Owner:** Member 3 (`R-METHOD`) · **Reviewer:** Member 1 (`R-LEAD`)
- **Blocked By:** `RL-016` (`#6`, `D-26`), `RL-100`, `RL-110` · **Blocks:** Final release
- **Deliverables:** `research/benchmarks/RELEASE_MANIFEST.md`.
- **Acceptance Criteria:** Packages generators, schemas, and validated suites `T`, `G`, `D`, `I` under the `D-26` license with checksums and documentation confirming zero `LoCoMo` (`CC BY-NC 4.0`) content reuse.
