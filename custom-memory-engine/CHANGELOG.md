# Changelog — Custom Memory Engine (`custom-memory-engine/`)

This file records the revision history of the Research Lab specification (`Graduation_Project_Proposal.md`, `Methodology_and_Consistency_Checklist.md`, `TECHNICAL_SPECIFICATION.md`, `PROJECT_ROADMAP.md`) and its supporting reference corpus (`reference/`).

---

## Post-Refactoring Gaps (28 October 2026)

- **Archival triggers moved from Python to schema.** `ensure_archival_trigger` no
  longer installs DDL; the `(S)∨(C)` triggers and their PL/pgSQL functions are now
  part of `001_full_schema.sql`, so a freshly migrated database is protected without
  any application call. The Python helper verifies presence only.
- **`guard_update` extended** to allow `reinforcement_count` to increment in-place,
  per Technical Specification §5 (duplicate reinforcement).
- **`engine/ingest.py` written.** `persist_turn` persists a turn idempotently.
  `apply_candidate` runs the validation gate and routes its verdict: commit/supersede
  for accepted candidates, hold/reject staging for held or rejected ones, in-place
  reinforcement for exact duplicates, and `plans_to` routing for planned utterances.
  `attach_embedding` writes the dense vector alongside a committed version.
  `derived_candidate` builds consolidation rows with `MEMORY_DERIVATION` lineage.
- **`deletion.scope="topic"` implemented.** Targets every version whose `memory_key`
  **or** `subject_key` matches the requested topic.
- **`evaluation/baselines/p_engine.py` written.** System `P` as a synchronous
  `MemorySystemAdapter` over the real schema, with an injected extractor callable,
  a private event loop for async↔sync bridging, and support for ablations `A1`–`A2b`
  and fusion modes `A5`/`A6`. Tested via `tests/integration/test_engine_adapter_db.py`.
- **`validation/contracts.py` allowlist updated** to include `P` so `HarnessConfig`
  with `system_id = "P"` validates.
- **`bitemporal_on_full.sql` corrected.** The archival assertion now archives
  `v1p` (superseded, branch S) rather than the currently-valid `v2p` (T1 case),
  which the new schema trigger correctly rejects.
- **43 bitemporal assertions still pass.** One assertion was replaced with a
  semantically equivalent one for the corrected subject; the count did not change.
- Test count: **1305 passed, 21 skipped** (integration, with container).

## Engine Refactoring and Read/Write Paths (28 October 2026)

- Merged the two import roots into one installable package. The engine core moved
  from `implementation/` to `src/custom_memory_engine/{engine,schema,clock,queue}/`;
  `pyproject.toml` previously packaged only the contracts module, so the engine was
  absent from a built wheel. Engine tests now live in `tests/` (pure) and
  `tests/integration/` (PostgreSQL). Paths named in earlier entries describe the
  tree as it stood when those changes were made.
- Removed the duplicate `PLAN/decisions/` memo copy and the redundant
  `implementation/tests/test_independence_boundary.py`, which duplicated
  `tests/test_boundaries.py` with a weaker check.
- Implemented the missing read path: `engine/retrieval.py` (exact dense cosine +
  `ts_rank_cd` lexical, fused by RRF with `k = 60`, `A5`/`A6` channel ablations) and
  `engine/render.py` (never renders superseded rows, omits ended validity in
  `current` mode, preserves rank order within `B_mem`).
- Implemented `engine/gate.py` (five ordered checks, `plans_to` routing, injection
  screen, `A3`/`A4` switches, one `MEMORY_CONFLICT` write) and
  `engine/deletion.py` (`R6` closure, bounded lineage walk, multi-source
  re-derivation, deletion epoch, content-free audit).
- Added the missing retrieval indexes plus a generated `memory_version.content_fts`
  `tsvector` column; the DDL previously declared no index at all. All 12
  `memory_version` writes now share one `_insert_version` helper, and a supplied
  `ClockTick` is validated against the transaction's own allocation.
- Measured, rather than asserted, retrieval latency and planner behaviour:
  `scripts/bench_retrieval.py`, results in `reports/benchmarks/`, rationale and
  open work in `REFACTORING.md`.

## RL-042 Transactional Supersession and Archival Procedures (27 October 2026)

- Added `implementation/engine/transactions.py` implementing close-then-insert single-valued supersession (`R1`–`R3`, `E1`, `E2`), transition-boundary revision (`E3`), candidate staging and `conflict_pending` promotion/rejection (`R4`), multi-valued termination (`R7`), and archival (`R5`, `Invariant 10`, `D-27`).
- Added `ensure_archival_trigger` and engine checks enforcing `(S) ∨ (C)` under per-user advisory locks and row locks (`FOR UPDATE`), preferring branch `'S'` when both `(S)` and `(C)` hold, rejecting `T1`/`T2` when neither holds, and preventing replacement mutations from leaving an already-archived `(C)`-branch source uncovered.
- Added ablation switches `A1` (`P-flat`), `A2` (`P-nohistory`), and `A2b` (`D-15`, disabled by default unless `allow_a2b=True`), along with unit and full-schema PostgreSQL integration tests in `implementation/tests/test_transactions.py` and `implementation/tests/test_transactions_db.py`.

## RL-033 Answer Scoring and LLM Judge Validation (27 October 2026)

- Added `evaluation/judge/deterministic.py` implementing normalized fact matching, effective-date verification (`what_changed` with partial-credit diagnostic), abstention detection, forbidden-claim and undisambiguated dual-value checks, and deterministic scorers for Suites `T`, `G`, `D`, and `I`.
- Added `evaluation/judge/llm_judge.py` and `evaluation/judge/validation_report.md` implementing `D-03` model separation (`gpt-4.1-2025-04-14` vs. `gpt-4.1-mini-2025-04-14`), same-family preference-bias disclosure, single-answer randomized presentation, length-normalization rubric prompts, successful-only SHA-256 response caching, `BudgetLedger` accounting with Tenacity retries, perturbation stability evaluation (`append_irrelevant_detail` and `paraphrase_hypothesis`), and `D-24` development-split validation (`Cohen's κ`, confusion matrices, and 95% cluster-bootstrap accuracy CIs). Live dual-annotator sign-off (`approvals_complete`) and the `RL-100` pilot freeze remain separate downstream gates.

## RL-044 Monotonic Clock and Atomic Queue (27 October 2026)

- Added transaction-consistent live/replay clock allocation with per-user advisory locking, `+1 µs` tie/out-of-order advancement and a durable content-free watermark in the existing audit table. Allocations synchronize the canonical database clock and record the clock mode without adding schema objects or changing migration fingerprints.
- Added typed idempotent enqueue and tenant-scoped `FOR UPDATE SKIP LOCKED` processing. Prepared database effects, clock allocation and job completion commit atomically; bounded Tenacity retries repeat fresh whole transactions only for serialization/deadlock failures. Semantic exclusion errors and exhausted jobs route to the canonical dead-letter state, and cancellation rolls back safely.
- Added pure and real PostgreSQL clock/concurrency/rollback/idempotency tests, including fresh-process persistence, actual unprivileged RLS, competing-worker retry races and deferred commit failures. See `evidence/RL-044-verification.md`. Provider/gate integration, production grants, deletion integration and the eight-worker load/research gates remain separate.

## RL-043 Temporal Query Predicates (27 October 2026)

- Added typed builders, composable bound SQL and asynchronous reads for `current_state`, `historical_as_of`, `historical_window` and inspector-only `audit_as_of`. Ordinary reads use recorded-range containment at the reference time, following the frozen Proposal/Technical Specification rather than the issue's stale open-upper-bound shorthand.
- Enforced point/window future guards and audit belief checks against the schema's database clock with deterministic SQLSTATE `22023`. Revalidation at compilation and execution prevents copied query objects from bypassing those guards. Explicit user filtering and existing tenant context compose with native RLS without changing caller identity or transaction ownership.
- Preserved every canonical version field and native range boundaries, including literal infinity versus unbounded endpoints. Added pure and full-schema PostgreSQL tests for the E1–E3 example, status visibility, replay clocks, real unprivileged RLS, schema qualification and transaction rollback. See `evidence/RL-043-verification.md`; ranking, rendering and later research gates remain separate.

## RL-034 Statistical Inference — Complete (27 October 2026)

- Added paired cluster inference, exact Clopper–Pearson bounds, the fixed global Holm family, and mechanical H1–H4 decision evaluators.
- Completed H3's three-way protocol: failure to establish ASR reduction is `Not supported`; LUAR intervals entirely beyond the frozen loss margin are `Not supported`; exact margin contact is `Inconclusive`; strict non-inferiority plus established ASR reduction is `Supported`.
- Added truth-table and exact-boundary regressions for H3. This software completion does not claim pilot thresholds, confirmatory results, or research approval.

## Post-merge Assurance (`RL-031`, `RL-041`; 27 October 2026)

- Retire completed replay generations through a local case-release hook and defer backend work to the next accounted operation. Raw stores, native Mem0 Qdrant/SQLite state, Graphiti clients/graphs, episode provenance and history diagnostics remain bounded while other active tenants are preserved. Every fallback adapter replacement is registered for final cleanup before success publication.
- Preserve the exact earliest fitting whole-turn suffix with certified linear token counting for the frozen tokenizer. Unknown codecs retain the exact scan; complete request-window checks remain in place. Work-counter, Unicode/window-boundary and cleanup regressions cover the merged implementation's remaining gaps.
- Include constraint-owning relations and internal foreign-key trigger enforcement in migration fingerprints. PostgreSQL regressions detect both false healthy states, refuse apply and verify explicit reset restores enforcement. Earlier pre-merge fingerprints require explicit recovery instead of being silently rewritten.

## RL-031 Baseline Adapters (26 October 2026)

- Added B0 explicit current-session, B1 window-bounded history with truncation diagnostics, B2 raw-turn cosine, B2h PostgreSQL English FTS/RRF60, real pinned Mem0 B3 and conditional Graphiti B4 adapters with shared frozen answering and local embedding services.
- Added immutable official ONNX/tokenizer asset preparation, complete overflow chunking, explicit public assembly, isolated vendor workers and per-attempt/episode token, latency, failure and cost accounting. SDK JSON modes, source timestamps and injected replay dates are preserved through checked integration seams.
- Extended harness context policies, clean question preparation, source-speaker handling, unavailable evidence metrics, adapter instrumentation and owned-resource cleanup before success publication. Optional dependencies remain separate from the default installation and root application.
- Added actual local HTTP, official model, PostgreSQL, Mem0/Qdrant/SQLite and Graphiti/FalkorDB software integration checks alongside boundary, budget, configuration, failure and cleanup regressions. Verification and immutable source provenance are recorded in `evidence/RL-031-verification.md`.
- Follow-up assurance corrected graph-recovery state loss when worker temporary-directory cleanup fails after an abnormal exit. Recovery now survives a file-lock failure and subsequent helper failure; regression checks cover retries and preservation of foreign graphs.
- Preserved the team's frozen split bytes across Windows checkouts with a split-scoped LF attribute; split assignments, hashes and sign-off records are unchanged.
- B4 D-21 inclusion, paid-provider availability, pilot parameters, research approvals, confirmatory/test runs and full-engine temporal/deletion conformance remain separate gates; no such outcome is claimed by these synthetic software tests.

## RL-041 Schema Migrations (26 October 2026)

- Added async, atomic schema apply, read-only version/status inspection and confirmed clean reset against explicit `RL_DATABASE_URL`, with canonical source checksums, private history, live catalog drift checks and a migration advisory lock.
- Kept shared extensions in public and reset only known owned objects with `RESTRICT`, preserving foreign dependencies through rollback. Added independent unit and real PostgreSQL tests for idempotency, concurrent apply, failed apply/reset, malformed history, first extension bootstrap and all 43 bitemporal assertions against the full schema.
- Extended isolated engine lint/type scopes to include the schema runner. Four equivalent replay-cache `isinstance` expressions use union syntax for compatibility with root CI's older Ruff. A scoped Git attribute preserves frozen split files' canonical LF bytes on Windows; their content and recorded hashes remain unchanged. Product code, dependencies and frozen research contracts remain unchanged.

## RL-025 Benchmark Adapters (26 October 2026)

- Added checksum-bound, read-only streaming loaders for LongMemEval oracle/S/M and LoCoMo, with source-scoped identities, stable D-23 replay, cumulative R3 tie handling and separate gold/scoring envelopes.
- Added turn-provenance Evidence Recall@k, multi-gold all-evidence-found diagnostics, primary exclusions and original-date sensitivity, plus exact licensed official QA function wrappers. Retained the historical Porter algorithm for reproducible LoCoMo stemming; provider CLIs and model downloads are excluded.
- Preserved LoCoMo corpus quirks (unpaired date metadata and compound/dangling evidence) after an offline mechanical schema audit, retaining all records and reporting unresolved evidence as unscored without changing official QA semantics.
- Corrected repeated LongMemEval source-session labels found during full S/M source verification: preserve each indexed occurrence and record an explicit replay permutation. Nested duplicate JSON keys now fail with validation errors outside parser C callbacks; regressions cover both cases.
- Integrated guarded selection artifacts binding source/split/suite/metric k, bounded per-history harness replay, original speaker metadata, source/scorer/clock provenance and synthetic end-to-end compatibility checks. Real baselines, provider judges, split freezes and research runs remain separate tasks.
- Pinned ijson and regex only in the isolated engine environment; extended default pytest collection to include benchmark-adapter tests. Product code and root dependencies remain unchanged. See research/benchmarks/ADAPTERS.md and evidence/RL-025-verification.md for contracts and verification limits.

## RL-030 Evaluation Harness Foundation (26 October 2026)

- Added the seven-method `MemorySystemAdapter` contract, explicit immutable model/system/configuration pins, typed replay inputs/results, and a source-checkout CLI with provider-free synthetic fixtures.
- Added exclusive UTF-8/LF run outputs, exact split/config provenance, artifact hashes, per-cluster/stratum summaries, measured timing in the manifest, and content-free failure markers. Success manifests are staged and synced before atomic publication.
- Implemented the RL-010 persistent campaign budget contract: reserve aggregate worst-case usage before dispatch, reconcile failed/retried calls, retain interrupted reservations, and enforce both token/USD caps plus the development allocation.
- Added fail-closed test-run authorization using explicit confirmation, typed human attestations, exact tagged freeze/list/split hashes, and a persistent local one-shot claim. These controls do not create actual research approvals or replace RL-100.
- Extended the dedicated workspace's lint, type and pytest scopes to include `evaluation/`; added synthetic determinism, failure, concurrency, path/junction and authorization regressions. Verification scope is recorded in `evidence/RL-030-verification.md`.
- Cleared pre-existing repository-wide CI lint/format failures in three research verification/source-fetch helpers with formatting/import-only changes; statistical and mechanical-check stdout remains byte-identical.
- Baselines, benchmark normalizers, extraction cache, provider judges, inferential statistics, actual pilot/test runs and H1–H4 outcomes remain separate tasks. Product runtime/configuration is unchanged.

## Wave 2 Implementation (`RL-011`, `RL-020`–`RL-024`) and P0/P1 Audit Fixes (25 October 2026)

1. **P0/P1 Audit & Consistency Fixes (`#1`–`#6`, `#9`–`#13`):**
   - Reconciled `TECHNICAL_SPECIFICATION.md` §9.3 with `Graduation_Project_Proposal.md` §§4.1 and 10.6 (3-test global Holm family across `H1` history-dependent superiority, `H2` intersection-union `max(p_A5, p_A6)` with both contrasts positive, and `H3` `ASR` reduction; paired `LUAR(P) − LUAR(A3) > −δ_luar`).
   - Hardened `schedule_replay` in `custom-memory-engine/src/custom_memory_engine/protocols.py` to reject turn timestamps that precede their session timestamp (`turn_timestamp_precedes_session_timestamp`), and extended `judge_gate` to support optional inter-human `κ ≥ 0.75`, mode coverage, and perturbation gates (`D-24`).
   - Expanded AST import-boundary tests (`RL-006`) across both `custom-memory-engine/` and `research-lab/` with synthetic forbidden-import regression tests, mirrored P1 decision memos into `research-lab/PLAN/decisions/`, and resynchronized `validation/document_hashes.txt`.
2. **Pre-Registered Pilot Protocol (`#14`, `RL-011`):**
   - Authored [`evaluation/pilot_plan.md`](evaluation/pilot_plan.md) and deterministic freeze functions in `custom_memory_engine/pilot_rules.py` for `D-09`–`D-14`, `D-21`, and `D-22`.
3. **Public Benchmark Acquisition & Verification (`#15` `RL-020`, `#16` `RL-021`):**
   - Implemented `research/benchmarks/longmemeval/{fetch.py,MANIFEST.txt,LICENSE,README.md}` and verified SHA-256 checksums and exact anomaly counts on `longmemeval_oracle.json` (`500 / 30 / 56 / 34 / 43`) and `longmemeval_s_cleaned.json` (`500 / 30 / 56`, `211` full-haystack / `39` evidence out-of-order, `76` full-haystack / `44` evidence `question_date < max(session_date)`, `95` tied-session histories).
   - Implemented `research/benchmarks/locomo/{fetch.py,MANIFEST.txt,LICENSE,README.md}` and verified `locomo10.json` (`10` conversations, `1,986` questions, `4` empty-evidence items, categories `1`–`5`: `282, 321, 96, 841, 446`).
4. **Own Synthetic Suite Generators (`#17` `RL-022`, `#18` `RL-023`, `#19` `RL-024`):**
   - Implemented deterministic seeded script-first generators, `MANIFEST.txt`, and `README.md` for `own_G` (write-governance with disjoint held-out test injection templates), `own_I` (cross-user isolation), `own_D` (deletion across 5 lineage structures and 7 in-boundary channels with `A8` positive-control leaks and re-learning / never-stated controls), and `own_T` (temporal-change covering `E1`–`E3`, `R1`–`R8`, `3:3` stratum balance, heuristic guards, and deterministic `audit_as_of` grid).

## P1 Implementation Review and Secure Local Configuration (24 October 2026)

- Reviewed all six issue acceptance criteria; #1–#5 are decision-preparation tasks with verified deliverables, while #6 explicitly requires Member 3/4 co-signatures and remains blocked. Downstream supervisor/peer approvals remain pending; no institutional signature is invented.
- Hardened agreement validation, UTC replay sorting and duplicate-session checks, local disposable database configuration/redaction, async connection cleanup, full rollback probe and server-observed writer contention.
- Created ignored local `custom-memory-engine/.env` with generated credentials and mode 0600; committed only the non-secret example. Tests now fail on warnings. Verification expanded to 48 passing tests plus lint/type/build and reference checks.
- Owner authorizes direct commit/push to main in place of the original PR workflow. Research freeze and full-engine production acceptance remain distinct future gates.

## P1 Decision Preparation and Owner Placement Revision (24 October 2026)

- Owner revised D-ARCH physical placement: new implementation and canonical P1 decision memos live in repository-root `custom-memory-engine/`; Research Lab remains references. Product imports/dependencies/database remain isolated.
- Prepared RL-010 and RL-012–RL-016 for GitHub #1–#6, with current values indexed in the decisions register and Technical Specification §2. No owner/peer/supervisor signature or institutional clearance was fabricated; paired approval and research-freeze gates remain pending.
- Added standalone locked CPython 3.12 runtime, replay/CP/judge protocol primitives, unit tests, and async Psycopg/PostgreSQL compatibility probes. These are P1 verification, not the later 12-table engine, T1–T5 deletion suite, benchmark adapters, human judge audit or eight-worker load test.
- Pinned PostgreSQL 16.15 / vector 0.8.7 image by verified registry digest; bounded evidence and execution report live under `custom-memory-engine/evidence/`. Existing reduced-schema archival behavior is not misrepresented as V4-safe.
- Clarified D-23 reference-time clamping includes recorded-clock tie increments. D-13/D-14 and other pilot thresholds remain unfrozen. No benchmark test splits were accessed.

## Pre-Implementation Documentation & Structure Refactoring (24 October 2026)

1. **Centralized Reference Directory (`reference/`):**
   - Moved `Source_Evidence_Ledger.md`, `Research_and_Fact_Check_Audit.md`, and `research/sources/` into `reference/` with a central index (`reference/README.md`) while preserving byte-identity of the V4 ledger and audit files.
   - Consolidated `review/` into `reference/TECHNICAL_FEASIBILITY_REVIEW.md` and `reference/ARCHITECTURE_DECISION_MATRIX.md`, removing redundant wrapper files (`review/README.md`, `review/FEASIBILITY_REVIEW_DECISIONS.md`, `review/EXISTING_WORK_IMPACT_ANALYSIS.md`).
2. **Technical Specification Corrections (`TECHNICAL_SPECIFICATION.md`):**
   - Unified §2 decision identifiers with `PLAN/DECISIONS_REGISTER.md` (`D-01`–`D-27`, `D-ARCH`, `D-ENG-01`–`D-ENG-04`).
   - Aligned §3.1 table definitions with the 12 canonical tables of `Graduation_Project_Proposal.md` §8 (`APP_USER`, `SESSION`, `TURN`, `PREDICATE_SCHEMA`, `MEMORY_VERSION`, `MEMORY_EVIDENCE`, `MEMORY_DERIVATION`, `MEMORY_EMBEDDING`, `MEMORY_CONFLICT`, `AUDIT_EVENT`, `DELETION_REQUEST`, `JOB`).
   - Fixed §3.4 rules `R7` (multi-valued predicates) and `R8` (`historical_as_of` rejects `t > ρ`; `audit_as_of` rejects `β > clock`) to match `Graduation_Project_Proposal.md` §7.4.
   - Corrected §3.5 query predicate signatures and SQL (`current_state(user_id, ρ)`, `historical_as_of(user_id, t, ρ)`, `historical_window(user_id, W, ρ)`, `audit_as_of(user_id, t, β)`) to match `Graduation_Project_Proposal.md` §7.4 and `validation/bitemporal_checks.sql`.
   - Clarified `MEMORY_DERIVATION(derived_version_id = r', source_version_id = r)` edge direction across §4 (Invariant 1) and §6 (Invariant 10 branch `(C)`).
   - Aligned Invariant 4 concurrency rules (`D-18`) between `TECHNICAL_SPECIFICATION.md` and `PROJECT_ROADMAP.md` §D.
3. **Planning & Workflow Consolidation (`PLAN/`):**
   - Merged `PLAN/TEAM_HANDOFF.md` into `PLAN/PROJECT_STATUS.md` as the single source of truth for execution status and first-wave dispatch.
   - Standardized the Phase P1 decision-record path to `research-lab/PLAN/decisions/` across `TASK_GRAPH.md`, `DECISIONS_REGISTER.md`, and issue drafts.
   - Resolved the artificial CI workflow blocker in `PLAN/INDEPENDENCE_BOUNDARY.md` §4 by explicitly permitting `.github/workflows/research-lab.yaml` scoped to `research-lab/`, and clarified §3.2 so standalone library dependencies in `research-lab/pyproject.toml` are governed by `PLAN/ARCHITECTURE.md` while imports from root `app/` remain strictly prohibited.
   - Refactored all issue drafts (`PLAN/issue_drafts/`) and updated the six published GitHub issues (`#1`–`#6`).

---

## Finalization Pass (18 October 2026)

Consolidated the V1–V4 revision trees into a single normative workspace:
- Promoted V4 documents (`Graduation_Project_Proposal.md`, `Methodology_and_Consistency_Checklist.md`, `Research_and_Fact_Check_Audit.md`, `Source_Evidence_Ledger.md`) to canonical status and removed superseded draft trees (`revised/`, `revised_v2/`, `revised_v3/`, `revised_v4/`).
- Added [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md), [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md), [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md), and the `PLAN/` execution workspace.
- Pinned `REFERENCE_LIST_SHA256 = a5851b94a6385e9ddea2480abce710d1c91ca0149923e04cda600f9a4254038f` in [`validation/mechanical_checks.py`](validation/mechanical_checks.py).

---

## V4 Patch Summary (17 October 2026)

Targeted patch of V3 resolving four audit issues without altering the 41-entry reference list:

1. **Historical-Memory Archival Bug Fixed (`Graduation_Project_Proposal.md` §§7.4 `R5`, 7.5, 7.6, 8 Invariant 10):**
   - Removed the V3 valid-ended archival branch `(A)` because archiving a *historical current belief* (`status = 'active'`, `recorded_range` open, `valid_range` ended `≤ ρ`) silently drops it from `historical_as_of` retrieval.
   - Restricted archival (`Active → Archived`) to two safe branches: **(S) Superseded** (`recorded_range` closed) or **(C) Active derived replacement** (`r'` with `MEMORY_DERIVATION(r' → r)` and `V(r') @> V(r)`).
   - Specified five deterministic regression tests (`T1`–`T5`, planned for `RL-090`) in `Methodology_and_Consistency_Checklist.md` §5 and `Graduation_Project_Proposal.md` §8 Invariant 10.
2. **Primary `H1` Contrast Aligned Around `P − A2` (`Graduation_Project_Proposal.md` §§3, 4.1, 10.3, 10.4, 10.6):**
   - Set `P − A2` (`P` vs `P-nohistory`) as the sole confirmatory contrast for `H1` across both co-primary strata (`history-dependent` superiority and `present-state` non-inferiority).
   - Demoted `P − A1` (`P-flat`) and `A2 − P-flat` to explicitly labeled diagnostics.
3. **Scenario-Level Inference for `H4` (`Graduation_Project_Proposal.md` §§4.1, 10.2, 10.3, 10.6):**
   - Clarified that the confirmatory Clopper–Pearson exact 97.5% upper bound (`≤ u*`) is computed at the **scenario level** (`N_D` independent scenarios), while event-level PDLR is reported only as a diagnostic.
4. **Source Ledger Reading Depth (`reference/Source_Evidence_Ledger.md` §§A, B):**
   - Added an explicit per-row **Reading depth** column using five strictly bounded categories (*metadata/abstract inspected*, *full text downloaded or retrieved*, *selected full-text passages read*, *full text read comprehensively*, *experiments independently reproduced*).

---

## V3 Patch Summary (17 October 2026)

1. **Stage-2 Full-Text Inspection (`[20]`–`[29]`):** Retrieved and inspected relevant full-text passages for the 10 2026 memory preprints (`reference/sources/fulltext/`), updating `Graduation_Project_Proposal.md` §§6.1–6.2 and `reference/Source_Evidence_Ledger.md` §E.
2. **Persona Layer Demoted to Optional (`A10`):** Specified three core memory layers (`Episodic`, `Semantic`, `Opinion / State`) and made `Persona` optional and disabled in `P` for confirmatory runs `H1`–`H4`, while retaining minimal episodic → semantic/summary consolidation for `H4` lineage deletion.
3. **Execution Claims Bounded:** Explicitly separated the executed reduced-schema SQL check (`validation/bitemporal_checks.sql`, 43 assertions on PostgreSQL 16.15) and `validation/cp_bounds.py` from unexecuted full-schema components and `H1`–`H4` experiments.

---

## V2 Patch Summary (16 October 2026)

1. **Executable Bi-Temporal Model (`Graduation_Project_Proposal.md` §§7.4, 8):** Defined `valid_range` and `recorded_range`, derived conditions (*current belief*, *superseded*, *currently valid*, *historical*), worked example `E1`–`E3`, rules `R1`–`R8`, and executed `validation/bitemporal_checks.sql` (`43/43` assertions passed).
2. **Predeclared Statistical Protocol (`Graduation_Project_Proposal.md` §§4.1, 10):** Defined `H1`–`H4` decision rules, cluster-bootstrap units, Holm–Bonferroni multiplicity control (`H2`), intersection-union testing (`H3`), and Clopper–Pearson bounds (`H4`, `validation/cp_bounds.py`).

---

## V1 Revision Summary (16 October 2026)

Initial restructuring of the original graduation project proposal to replace unverified performance numbers with falsifiable hypotheses (`H1`–`H4`), explicit storage boundaries (`§7.7`), and a 41-entry verified reference list (`reference/Source_Evidence_Ledger.md`).
