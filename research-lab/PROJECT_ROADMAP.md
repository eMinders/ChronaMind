# PROJECT_ROADMAP.md

**Project:** Personal Digital Twin Agent with Temporally-Aware, Governed Long-Term Memory
**Authoritative proposal:** [`Graduation_Project_Proposal.md`](Graduation_Project_Proposal.md)
**Revision history and finalization pass:** see [`CHANGELOG.md`](CHANGELOG.md). Earlier revisions (V1 first revision, V2 patch, V3 patch, V4 patch) were consolidated into this working set during the finalization pass; the standing documents at the repository root are the single authoritative copy.
**Companion specifications:** [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md), [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md).
**Status:** this is an execution guide written against the repository state at the end of the finalization pass. It describes **planned** engineering and research work; it does **not** report new experimental results. Every "Status" line below is grounded in artifacts in the repository.

---

**Execution navigation (2026-10-23):** owner-selected Hybrid and S-07 disposition: [review decisions](review/FEASIBILITY_REVIEW_DECISIONS.md). Engineering contracts/version policy: [PLAN/ARCHITECTURE.md](PLAN/ARCHITECTURE.md). Task prerequisites are canonical in [PLAN/TASK_GRAPH.md](PLAN/TASK_GRAPH.md); diagrams/sequence in [PLAN/DEPENDENCY_MAP.md](PLAN/DEPENDENCY_MAP.md); actual approvals/status in [PLAN/PROJECT_STATUS.md](PLAN/PROJECT_STATUS.md). Historical baseline claims below are not new completion evidence. RL-001 team approval is owner-confirmed and recorded in PLAN/PROJECT_STATUS.md §6; individual responses and reviewed-revision audit details were not supplied. No new Frozen row or experiment is implied.

## A. Project mission and boundaries

### A.1 Problem

LLM assistants over long horizons either forget between sessions or store user information as flat text, losing the ability to distinguish an old preference from a current one, to say *why* something is remembered, or to reliably forget it on request ([`Graduation_Project_Proposal.md`](Graduation_Project_Proposal.md) §1).

### A.2 Research contribution

The project is **fundamentally a memory-engine research project**. The contribution is an open, reproducible, ablation-controlled evaluation of three design choices under one protocol:

1. **Bi-temporal versioning of memories**, measured as the incremental effect of retaining version history *beyond* valid-time filtering (H1).
2. **A write-time validation gate** (H3).
3. **Lineage-tracked deletion** of a memory and the artifacts derived from it (H4).

Hybrid dense + lexical retrieval under the temporal predicate matching the question is also measured (H2). The research questions, hypotheses, endpoints, and decision rules are frozen in Sections 4, 4.1, and 10 of the proposal and in Section 1 of [`Methodology_and_Consistency_Checklist.md`](Methodology_and_Consistency_Checklist.md).

### A.3 Relationship between the memory engine and the Personal Digital Twin application

The engine is the research artifact. The **Personal Digital Twin (PDT) application** is a conservatively-defined demonstration (§1.1, §11 of the proposal) that *uses* the engine; it is **not** the primary research contribution and its evaluation does **not** substitute for engine evaluation. The persona layer that would ordinarily make an application "twin-like" is **optional** throughout — it is off in configuration P for H1–H4 and is evaluated only as the exploratory ablation A10. The research success criteria (H1–H4 decision rules) are independent of whether the PDT application exists.

### A.4 What the project does and does not claim

- Does claim: a measurable comparison between the proposed engine and ablated variants, under a predeclared protocol, with hedged "supported / inconclusive / not supported" decisions per hypothesis.
- Does **not** claim: that the proposed engine will outperform any external system (§4.1 last paragraph of the proposal explicitly states "The proposal does not predict that P will outperform any external system"); that deletion is *guaranteed* (§7.7, §10.3, §10.6 "What the H4 bound means"); that any paper's reported results have been independently reproduced (`Source_Evidence_Ledger.md` Scope note; no row carries the depth label *experiments independently reproduced*).

### A.5 Expected final deliverables

As listed in §12 of the proposal and in §J of this roadmap, bundled into: a tested memory-engine codebase; reproducible evaluation harnesses for H1–H4 (own suites plus LongMemEval and LoCoMo adapters); predeclared metric and statistics code; experiment reports; a PDT demonstration application; test and preservation evidence; a final report and defense presentation.

### A.6 Research success vs application completeness

A **research-success** outcome for a given hypothesis is a decision ("Supported", "Not supported", or "Inconclusive") that follows mechanically from the predeclared decision rule in §4.1 of the proposal and the executed experiment. Application completeness is a separate axis: the PDT application is called complete when the user flows in §11 are implemented, tested, and demonstrate the engine's provenance, correction, historical-question, and deletion behaviours; it is not required for a defensible H1–H4 result.

---

## B. Current state and verified baseline

Every claim here is traceable to a file under this repository.

### B.1 Repository documents (standing authoritative set)

| Deliverable | Path | Status |
|---|---|---|
| Authoritative proposal | `Graduation_Project_Proposal.md` | 16 sections + 41-entry reference list. Mechanical checks pass. |
| Methodology and consistency checklist | `Methodology_and_Consistency_Checklist.md` | Current. |
| Research and fact-check audit | `Research_and_Fact_Check_Audit.md` | Current; carries the historical V2/V3/V4 patch-review findings as its historical sections. |
| Source evidence ledger | `Source_Evidence_Ledger.md` | Current; carries the per-row **Reading depth** column. |
| Changelog | `CHANGELOG.md` | Current; records the finalization pass and the earlier V2/V3/V4 patches. |

Earlier revisions (V1 first revision, V2 patch, V3 patch, V4 patch) were deleted during the finalization pass. The diffs they recorded are preserved as historical sections inside `CHANGELOG.md`, `Research_and_Fact_Check_Audit.md`, and `Methodology_and_Consistency_Checklist.md`.

### B.2 Code and executable artifacts in the repository today

| Artifact | Path | What it does | Status |
|---|---|---|---|
| Bi-temporal reduced-schema SQL check | `validation/bitemporal_checks.sql` | Exercises the worked example, CHECK/exclusion constraints, R1/R2 same-day/R3 clock-tie/R4 held promotion/R5 archival status-only/R6 key-wide delete/R8 t>ρ, under PostgreSQL 16 | **Executed in V2**; log `bitemporal_checks.output.txt` reports 43 PASS lines, final line `ALL ASSERTIONS PASSED`. **Not re-run in V3, V4, or the finalization session.** |
| Clopper–Pearson arithmetic | `validation/cp_bounds.py` | Zero-leak upper bounds and clustering illustration | **Reproducible** — output `cp_bounds.output.txt` was re-generated byte-identically in the finalization session (`diff` returned 0) using `python3 validation/cp_bounds.py`. |
| Mermaid syntax check | `validation/mermaid_parse.cjs` | Parses the five diagrams | **Executed in V2** (5 of 5 blocks parsed, saved in `mermaid_parse.output.txt`); not re-executed since (requires `mermaid` + `jsdom` node modules). |
| Mechanical consistency script | `validation/mechanical_checks.py` | Citations, cross-refs, anchors, tables, ablations, stale-wording guard, H1/H4 wording, persona, excluded terms, fabricated-results guard, reference-list byte-identity to V3 | **Executed in the finalization session.** All hard checks pass (see `validation/mechanical_checks.output.txt`, final line `SUMMARY: ALL HARD CHECKS PASSED`). |
| Document hashes | `validation/document_hashes.txt` | MD5 of each authoritative document, written at the end of the finalization pass. Any legitimate change bumps a hash and must be recorded in `CHANGELOG.md`. | **Executed in the finalization pass.** |
| Full-text source corpus | `research/sources/fulltext/` | PDFs and `pdftotext -layout` extracts for refs 20–29 plus `MANIFEST.txt` with SHA-256 hashes | Preserved from V2 Stage 2 (17 Oct 2026). |
| Fetched research pages | `research/sources/pages/` | Author, publisher, repository, benchmark, docs, and license pages | Preserved from the first revision and V2 stages. |

### B.3 Implemented vs specified

**Specified only (not implemented in this repository):** the full application schema (§8 of the proposal), the memory engine code (fast path, slow path, extraction, validation gate, retention, consolidation, lineage tracker, deletion closure), the PostgreSQL job queue with `SKIP LOCKED` and idempotency keys, pgvector-backed dense retrieval, hybrid retrieval with RRF, row-level security, the harness replay clock, the H1–H4 evaluation harnesses and own-suite generators, the PDT application.

**Implemented and executed:** only the reduced-schema SQL check (V2), the Clopper–Pearson arithmetic (V2, re-verified in finalization), the mermaid syntax check (V2), the mechanical consistency script (V4 + finalization), and the preservation record (V4 + finalization).

### B.4 Unexecuted tests and experiments

- The five V4 archival-precondition tests **T1 – T5** (§7.5, §8 invariant 10 of the proposal; §5 of the checklist).
- The full-schema SQL (invariants 1, 7, 8 procedure, 9, 10).
- RLS tests, job-queue idempotency tests, concurrent-writer tests, pgvector + hybrid retrieval tests, deletion-closure tests, cross-user isolation tests.
- Research experiments H1, H2, H3, H4.
- Judge validation on the development split, pilot study, own-suite generation and verification, baseline integration (B3 Mem0 pinned; B4 Graphiti conditional).

### B.5 Open decisions carried from V2/V3/V4

Verbatim list in §6 of `Methodology_and_Consistency_Checklist.md`; summarised in §E below.

### B.6 Known risks and limitations

See §13 of the proposal (risk register) and §10.6 ("What the H4 bound means"), §7.7 ("Deletion scope"), §10.5 (judge validation caveats). The V4-added risk is item 17 (when/how to execute T1 – T5).

### B.7 Honest completion statement

No overall "percent complete" figure is given. Instead: the **specification** is at V4 and consistent; the **implementation** of the memory engine and application is at design stage; the **validation code** covers a reduced schema plus mechanical consistency only; the **research experiments** have not been run.

---

## C. End-to-end project phases

Dependencies determine the order; some phases run in parallel where their prerequisites allow it.

| # | Phase | Depends on | Can run in parallel with |
|---|---|---|---|
| P0 | Repository baseline and research-specification freeze | — | — |
| P1 | Resolve remaining technical decisions | P0 | P2, P3 starts |
| P2 | Dataset and benchmark preparation (LongMemEval, LoCoMo; licenses, splits, adapters) | P0 | P3, P4 starts, P5 |
| P3 | Evaluation harness and baseline implementation (B0, B1, B2, B2h, B3 Mem0; B4 Graphiti conditional) | P0, parts of P1 | P2, P5 |
| P4 | Memory-engine schema and core temporal semantics (full schema, invariants 1–10, bi-temporal predicates) | P0, P1 (version pinning) | P2 |
| P5 | Validation gate, contradiction handling, version lifecycle (R1–R6, assertion-mode classification, calibration) | P4 | P2, P6 starts |
| P6 | Retrieval, consolidation, provenance lineage (dense + lexical + RRF; derivation links) | P4, P5 | P7 starts |
| P7 | Deletion and privacy enforcement (R6 closure, storage boundary, Section 7.7) | P4, P5, P6 | — |
| P8 | Integration and asynchronous processing (fast/slow paths, job queue, idempotency, visibility lag) | P4–P7 | — |
| P9 | Unit, regression, integration, concurrency, security testing (including T1–T5) | P4–P8 | P10 harness |
| P10 | Controlled experiments and ablation evaluation (H1–H4 on own + public suites) | P2, P3, P4–P9, pilot | — |
| P11 | Statistical analysis and error analysis | P10 | documentation |
| P12 | Personal Digital Twin demonstration application | P4–P8 (engine stable) | P10 (independent) |
| P13 | Documentation, reproducibility, packaging, defense | P0–P12 as available | — |

Phase status at the end of the finalization session: **P0 complete; P1 open; P2–P13 not started.**

---

## D. Detailed instructions for every phase

Each phase lists Purpose · Starting state · Activities · Technical design · Research justification · Inputs/dependencies · Outputs · Acceptance criteria · Validation · Failure modes · Fallback · Completion evidence.

### D.0 Phase P0 — Repository baseline and research-specification freeze

- **Purpose:** establish a single authoritative working set from which all later work branches, with validation artefacts preserved, filenames normalised, and obsolete revision directories removed.
- **Starting state:** the V4 patch files produced by the previous session, plus the V1/V2/V3 revision directories carried forward for provenance.
- **Activities:**
  1. Fix any stale version-label defects in `validation/mechanical_checks.py` (notably the self-description that referred to earlier revisions).
  2. Promote the V4 proposal, checklist, audit, source evidence ledger, and changelog to version-free filenames at the repository root.
  3. Move the reusable validation artefacts (SQL + 43-assertion log, Clopper–Pearson arithmetic, mermaid syntax check, mechanical consistency script, bi-temporal log inspection) into `validation/` under version-free filenames.
  4. Move the source evidence (full-text extracts for refs [20]–[29], fetched benchmark/publisher/documentation pages for refs [1]–[41]) into `research/sources/fulltext/` and `research/sources/pages/`.
  5. Delete the obsolete V1, V2, V3, and V4 revision directories and the stale symlink layout.
  6. Update every in-text file path, command example, and cross-reference in the standing documents to the new paths.
  7. Re-run `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` and preserve the output.
  8. Record the document hashes (`validation/document_hashes.txt`).
- **Technical design:** the finalization is driven by `scripts/finalize_repository.py`. The mechanical consistency script's reference-list byte-identity check (previously a cross-file comparison against the deleted V3 proposal) is replaced by a pinned SHA-256 of the 41 reference entries (`REFERENCE_LIST_SHA256`); the property enforced is unchanged.
- **Research justification:** gate for the whole project — without a frozen, validated baseline, every later experiment's provenance is contested.
- **Inputs/dependencies:** the V4 patch artefacts and the preserved source evidence.
- **Outputs:** standing authoritative documents at the repository root; `validation/`; `research/sources/`; `scripts/finalize_repository.py`; `validation/mechanical_checks.output.txt`; `validation/document_hashes.txt`; this roadmap; `TECHNICAL_SPECIFICATION.md`; `TRACEABILITY_MATRIX.md`; `README.md`.
- **Acceptance criteria:**
  - Mechanical check ends with `SUMMARY: ALL HARD CHECKS PASSED`.
  - `python3 validation/cp_bounds.py` produces output byte-identical to `validation/cp_bounds.output.txt`.
  - `validation/bitemporal_checks.output.txt` reports 43 PASS lines and ends with `ALL ASSERTIONS PASSED`.
  - No obsolete revision directory remains in the workspace.
  - `scripts/finalize_repository.py` is idempotent: a second run reports "Workspace already in its final shape".
- **Validation:** the four outputs above plus the dangling-reference scan built into `scripts/finalize_repository.py`.
- **Failure modes:** rewriting the mechanical-check baseline is a specification change (now a pinned hash); any legitimate reference-list change must bump `REFERENCE_LIST_SHA256` and record the diff in `CHANGELOG.md`. Moving source evidence before deleting the parent directories could lose data; the script orders the operations deliberately.
- **Fallback:** the finalization script is dry-run by default; a reviewer runs it without `--apply` first to confirm the plan.
- **Completion evidence:** `validation/mechanical_checks.output.txt` final line `SUMMARY: ALL HARD CHECKS PASSED`; `validation/document_hashes.txt` present; the dangling-reference scan in `scripts/finalize_repository.py` reports no stale paths in the authoritative documents.
- **Status: COMPLETE** at the end of the finalization pass (see §B.2).

### D.1 Phase P1 — Resolve remaining technical decisions

- **Purpose:** turn the 17 Open decisions in `Methodology_and_Consistency_Checklist.md` §6 into frozen, dated, justified choices before experiments start.
- **Starting state:** 17 Open items (see §E below for the full list and the two V4-added items 15 revised and 17).
- **Activities:**
  1. For each decision in §E, record **current value, evidence, alternatives considered, reason for selection, consequences, supervisor sign-off**, in `TECHNICAL_SPECIFICATION.md` §2 ("Decision log").
  2. For decisions that require a pilot (margins δ, δ_pres; u*; detection share; stratum mix; Graphiti feasibility; PostgreSQL version), schedule the pilot as part of P3/P10 and keep the decision Open until the pilot result is attached.
  3. Attach the supervisor's approval or minutes to each decision that requires it (ethics, LLM budget, benchmark release, judge agreement criterion, inclusion of persona layer).
- **Technical design:** no code; it is a documentation and sign-off phase.
- **Research justification:** every §4.1 success rule depends on §10.7 freeze values; §10.6 bounds depend on §4.1 u*; §10.6 non-inferiority depends on δ, δ_pres.
- **Outputs:** `TECHNICAL_SPECIFICATION.md` §2 populated; supervisor approvals recorded; updated `CHANGELOG.md` (or a successor `CHANGELOG_v5.md` only if V4 wording must change) if any decision reclassifies a V4 claim.
- **Acceptance criteria:** every item in §E has either a Frozen row with evidence or a dated Pilot row with an owner and target date, and no §4.1 decision rule still refers to an undeclared value.
- **Validation:** cross-check against §4.1 and §10.7 of the proposal; mechanical check still passes.
- **Failure modes:** a chosen value violates an existing invariant (recheck invariants 1–10); a supervisor rejection forces a methodology rewrite (treat as a V5 patch, repeat the fact-check audit).
- **Fallback:** if a value cannot be fixed, mark the related hypothesis as "deferred" rather than silently using a default.
- **Completion evidence:** `TECHNICAL_SPECIFICATION.md` §2 complete; supervisor-approval memo attached.
- **Status: NOT STARTED.**

### D.2 Phase P2 — Dataset and benchmark preparation

- **Purpose:** make LongMemEval_S (H2), LongMemEval overall (descriptive), and LoCoMo (descriptive) available in a reproducible, licensed, and clearly-attributed form, and generate own suites T, D, G.
- **Starting state:** LongMemEval MIT-licensed; dataset card and oracle counts already saved (`research/sources/pages/`); LoCoMo CC BY-NC 4.0; licence file saved. Own-suite generators do not exist yet.
- **Activities:**
  1. Pin the LongMemEval cleaned release (commit `98d7416`, 19 Sep 2025) and the LoCoMo release (`locomo10.json` as checked); mirror into `research/benchmarks/longmemeval/` and `research/benchmarks/locomo/` (new directories) with licence files, SHA-256 hashes, and `MANIFEST.txt`.
  2. Implement benchmark adapters that **do not alter the data**. Adapter responsibilities: tokenization/formatting, split loading, oracle lookup, scoring call (official for both benchmarks — LongMemEval `evaluation.py`, LoCoMo evaluator).
  3. Write generators for the own suites T (temporal), D (deletion), G (gate/injection) per §10.2 of the proposal; the generators must emit scenarios, scored questions, canaries, control probes, labels, and seed metadata.
  4. Freeze development vs test splits (§10.7); publish the freeze list under `research/benchmarks/splits/`.
  5. Independent re-count of LongMemEval oracle (30 abstention, 56 single-session-assistant, 470 answerable; `Source_Evidence_Ledger.md` §C row); re-count of LoCoMo (10 conversations, 1,986 questions, four questions with empty evidence).
- **Technical design:** adapter interface = `(system, question, gold) → score` so B0–B4 and P share identical scorer calls; generators seeded; splits by scenario-id hash to prevent leakage.
- **Research justification:** H2 primary endpoint is on LongMemEval_S; H1, H3, H4 primary endpoints are on the own suites; LoCoMo is descriptive only (its 10 conversations are too few for cluster inference).
- **Inputs/dependencies:** mirror preparation requires RL-002 + RL-003 (runtime RL-013 precedes manifest); own generators require their RL-014/016 decisions; adapters require RL-030 scorer interface. Final subset/split acceptance retains the declared research gates; PG selection does not itself gate mirroring.
- **Outputs:** `research/benchmarks/{longmemeval,locomo,own_T,own_D,own_G}/` with manifests, licences, adapters, generators, splits, and seed logs.
- **Acceptance criteria:**
  - MD5/SHA-256 of pinned data matches the Source Evidence Ledger counts.
  - Adapter runs the official scorer and reproduces published metric values on a trivial submission (e.g. B0 no-memory) to within rounding.
  - Generators are deterministic given a seed; a smoke run produces the expected scenario/event/canary counts.
- **Validation:** `pytest` module per benchmark; data-integrity hashes checked in CI.
- **Failure modes:** benchmark updated after freeze (pin by commit hash); own-suite generator produces unreachable canaries (detect via the §4.1 "mechanically guaranteed" audit — reject that scenario).
- **Fallback:** if LongMemEval access is unavailable, degrade H2 to a smaller declared subset per §6 open decision 8.
- **Completion evidence:** `MANIFEST.txt` hashes present; smoke-test adapter log stored.
- **Status: NOT STARTED.**

### D.3 Phase P3 — Evaluation harness and baseline implementation

- **Purpose:** build the harness that runs any configuration (P, A1…A10, A2b, B0, B1, B2, B2h, B3, B4 if included) against any suite, with the official scorer, judge calls, and statistics module.
- **Activities:**
  1. Implement a configuration layer with a pinned model and a pinned prompt per configuration (§10.4). Record model id, decoding temperature, and seed.
  2. Implement baselines: B0 (no memory, just the LLM); B1 (full in-context history); B2 (dense RAG over raw turns); B2h (hybrid dense + lexical over raw turns); B3 Mem0 at a pinned commit; B4 Graphiti at a pinned commit conditionally.
  3. Implement the LLM-judge harness per §10.5 (reference-based grading; perturbation calibration from `[36]`).
  4. Implement the statistics module per §10.6: cluster bootstrap, sign-flip permutation, Holm correction, Clopper–Pearson at scenario level, non-inferiority at margin.
  5. Implement a run manifest: config hash, data hash, seed, timing, cost, env capture.
- **Technical design:** `runs/<timestamp>/<config>/<suite>/{answers.jsonl, scores.jsonl, per_cluster.jsonl, manifest.json}`.
- **Research justification:** without a shared harness, baseline parity (§10.1) is unverifiable.
- **Outputs:** `evaluation/` directory with `harness/`, `baselines/`, `judge/`, `stats/`, `runs/`.
- **Acceptance criteria:** running any baseline on any suite produces manifests and byte-identical scores on repeat with the same seed; running P on suite T with placeholder data produces the expected two-stratum output tables.
- **Validation:** unit tests per baseline and scorer; reproducibility test runs two seeds and asserts determinism modulo documented randomness.
- **Failure modes:** scorer version drift (pin + hash); API nondeterminism (log model id and temperature; use deterministic parameters where possible).
- **Fallback:** if an API model becomes unavailable, swap in the next-ranked pinned alternative and record the swap in the manifest and the decision log.
- **Completion evidence:** `evaluation/runs/<timestamp>/baselines/smoke/` reports present and reproducible.
- **Status: NOT STARTED.**

### D.4 Phase P4 — Memory-engine schema and core temporal semantics

- **Purpose:** implement the full data model of §8 and the retrieval predicates of §7.4 and §7.6, with every invariant 1 – 10 enforced somewhere (constraint, trigger, transactional procedure, or scheduled integrity check).
- **Activities:**
  1. Author the full schema in `implementation/schema/` (DDL) including `MEMORY`, `MEMORY_VERSION`, `PREDICATE`, `MEMORY_EVIDENCE`, `MEMORY_DERIVATION`, `DELETION_LOG`, `JOB`, audit tables.
  2. Add CHECK constraints for the staging/open-range rules (§7.4 R-rules), the exclusion constraint with `btree_gist` for single-valued predicates on both ranges (§8 invariant 4), the per-user partition keys, the `recorded_from` monotonicity trigger.
  3. Implement the transactional supersession, archival, and deletion procedures.
  4. Implement the three retrieval predicates (current-state, historical-as-of point and window, audit-as-of).
  5. Add migrations under `implementation/migrations/` with up-and-down scripts.
- **Technical design:** supported stable PostgreSQL/pgvector target verified under RL-012 and PLAN architecture §7, with `btree_gist`, `tsvector`; historical PG 16.15 is not the selected runtime. Proposal §8 contains the entity–relationship diagram; spec §3 supplies normative requirements.
- **Research justification:** every hypothesis depends on the engine behaving as specified; the specification is only as strong as the implementation that enforces it.
- **Outputs:** `implementation/schema/*.sql`, `implementation/migrations/`, `implementation/engine/*` (Python or chosen language).
- **Acceptance criteria:** full-schema SQL runs cleanly against a target PostgreSQL version in CI; every invariant is enforced; the V2 reduced-schema SQL assertions still pass when run against the full schema; the T1–T5 harness scaffolding exists.
- **Validation:** `pytest` + SQL fixtures; schema diff vs §8.
- **Failure modes:** exclusion constraint infeasible on the full schema under concurrent writers (§6 open decision 6) — pilot and fall back to serialisable isolation in the supersession procedure.
- **Fallback:** investigate transaction ordering/isolation and retest invariant 4 under contention, retaining the spec §3.2 mandatory exclusion constraint. The proposal's serialisable-only alternative conflicts with that MUST if it removes the constraint; report and propose a spec §13-controlled change before adopting it.
- **Completion evidence:** `implementation/tests/schema/` run report; `invariants_matrix.md` showing enforcement site per invariant.
- **Status: NOT STARTED.**

### D.5 Phase P5 — Validation gate, contradiction handling, version lifecycle

- **Purpose:** implement §7.5 (gate), the conflict relations, and the R1–R6 lifecycle.
- **Activities:** assertion-mode classifier (hedge/joke/quote/assistant-guess/injection/user-authored); extractor confidence calibration pre-pilot; gate decisions logged with a reason code per §7.5 flow; conflict-detector that proposes supersessions via the transactional procedure in P4.
- **Acceptance criteria:** given a stream of synthetic candidates, the gate assigns the labelled outcome in ≥ a pilot-determined accuracy; a known contradiction triggers the supersession procedure once and only once.
- **Validation:** gate-decision unit tests; property-based tests for idempotency.
- **Failure modes:** false updates leak into current beliefs — surfaced by H3 ASR.
- **Fallback:** more conservative accept threshold; add a human-in-loop review queue (optional per §10 and §11).
- **Status: NOT STARTED.**

### D.6 Phase P6 — Retrieval, consolidation, provenance lineage

- **Purpose:** implement §7.6 retrieval, minimal consolidation (essential per the V4 decision), and `MEMORY_DERIVATION` lineage.
- **Activities:** dense (pgvector exact per user; HNSW only in scalability tests with recall checks per §9); lexical (PostgreSQL full-text; not called BM25 per audit row 6); RRF fusion with k = 60 per `[12]`; minimal consolidation (semantic summaries and derived memories) with derivation links; the retention variant A9 as an exploratory ablation only.
- **Acceptance criteria:** evidence Recall@k and MRR match the harness definitions; derivation links point back to source `MEMORY_VERSION` rows.
- **Validation:** retrieval-correctness tests; snapshot-consistency test (fast-path reads do not observe partial supersession).
- **Failure modes:** approximate index drops rows post-filter (pgvector README); mitigated by exact search per user.
- **Fallback:** exact-per-user retrieval remains confirmatory P; approximate iterative scans are scalability-only with a declared recall-against-exact floor and verified extension support.
- **Status: NOT STARTED.**

### D.7 Phase P7 — Deletion and privacy enforcement

- **Purpose:** implement rule R6 and the §7.7 deletion closure within the defined storage boundary; keep audit free of content or hashes.
- **Activities:** compute the transitive derivation closure in a single transaction; remove all versions, derived artifacts, lineage rows, queue entries, and in-boundary cache entries; write a boundary-free audit record (no content, no hash); schedule physical cleanup for backups per retention schedule; disallow re-ingestion within the deletion epoch (§7.7).
- **Acceptance criteria:** T5 passes; every in-boundary channel listed in §7.7 ("Deletion channels") shows no residual retrievable copy after a deletion; a never-stated control probe is never answered.
- **Validation:** T5, plus the suite D harness (positive control A8 P-nolineage must detect leakage in the predeclared minimum share of scenarios; §4.1 "Supported" criterion requires this to hold).
- **Failure modes:** indirect leakage via cached embeddings or search caches (fix: deletion propagates to vector-store rows and cache invalidation).
- **Fallback:** per-user vector-store partition that can be dropped atomically.
- **Status: NOT STARTED.**

### D.8 Phase P8 — Integration and asynchronous processing

- **Purpose:** wire the fast and slow paths of §7.1–§7.2; implement the PostgreSQL job queue with `FOR UPDATE SKIP LOCKED`, idempotency keys, per-user advisory locks, and the dead-letter table.
- **Activities:** fast-path retrieval returns within the §9 latency envelope while the slow path extracts, validates, and versions in the background; visibility lag is measured descriptively (§10 O6).
- **Acceptance criteria:** idempotency test: same event re-enqueued N times produces exactly one belief transition.
- **Validation:** concurrency tests (two workers on the same user), replay-clock harness.
- **Status: NOT STARTED.**

### D.9 Phase P9 — Unit, regression, integration, concurrency, security testing

See §G for the full testing matrix. Phase P9 is complete when every row in §G is either **Tested** or has a documented, approved waiver.

### D.10 Phase P10 — Controlled experiments and ablation evaluation

- **Purpose:** execute H1 – H4 per §10.
- **Activities:** run the pilot on the development split; freeze δ_pres, δ (LUAR), u*, the detection share, the stratum mix of suite T, the LongMemEval subset (if any), and the Graphiti inclusion decision; run the test split exactly once per configuration; archive run manifests.
- **Acceptance criteria:** each hypothesis has a decision (Supported / Not supported / Inconclusive) per §4.1 derived mechanically from the preregistered rules, not by post-hoc choices.
- **Validation:** rerun on a different seed produces the same decision (sensitivity analysis) — not a confirmation of effect size.
- **Status: NOT STARTED.**

### D.11 Phase P11 — Statistical analysis and error analysis

- **Purpose:** cluster-bootstrap confidence intervals, sign-flip permutation tests, Clopper–Pearson intervals, Holm correction, intersection–union logic; qualitative error analysis.
- **Acceptance criteria:** the §10.6 statistics module output matches the frozen rule; error analysis includes a sample of answered and unanswered items per stratum.
- **Status: NOT STARTED.**

### D.12 Phase P12 — Personal Digital Twin demonstration application

- **Purpose:** demonstrate the engine with the §11 user flows.
- **Activities:** inspector view (browse by layer, timeline with valid-time and recorded-time axes); provenance panel (why a memory is here, who said it, when); correction flow (user asserts a replacement; triggers the gate and the supersession procedure); historical query view ("what did you believe about X on date Y?"); deletion flow with a confirmation panel and audit record; uncertainty cues for inferred items; error handling and observability dashboard.
- **Acceptance criteria:** each §11 user flow has an acceptance test; privacy and security boundaries hold under cross-user tests (§G).
- **Fallback:** persona layer stays off (default per §10.4); application can ship without it.
- **Status: NOT STARTED.**

### D.13 Phase P13 — Documentation, reproducibility, packaging, defense

- **Purpose:** publish reproducible artifacts (schema, migrations, harness, suites, pinned configs, manifest hashes), a run-your-own-eval walkthrough, the final report, the defense deck, and the limitations statement.
- **Outputs:** `release/vX.Y.Z/` tag with schema, code, configs, suites, docs, licence files; final report; presentation.
- **Acceptance criteria:** a reviewer with only the release and a PostgreSQL instance can rerun a smoke evaluation and reproduce the smoke summary.
- **Status: NOT STARTED.**

---

## E. Research specification and technical decisions

Values below are either **Frozen** (set in a V4 document and not Open) or **Open** (listed in `Methodology_and_Consistency_Checklist.md` §6). The "Where / Evidence" column cites the V4 document that fixes the value. No value is marked Frozen unless it is already set in a V4 document.

| # | Decision | State | Current value | Where / Evidence | Alternatives | Reason for selection | Consequences | Pilot/approval required |
|---|---|---|---|---|---|---|---|---|
| 1 | H1 primary contrast | Frozen | **P − A2** | Proposal §4 RQ table, §4.1, §10.6 units table, §10.4 (A2); checklist §0 and §1 | P − P-flat (combined effect) | Isolates incremental effect of retained history beyond valid-time filtering (checklist §0) | A2 cannot answer history-dependent questions → non-inferiority on present-state is the substantive test | Supervisor confirmation (open decision 1 in checklist §6) |
| 2 | H1 endpoint form | Frozen | **Two co-primary strata: history-dependent (superiority) + present-state (non-inferiority at δ_pres)** | Proposal §4, §4.1, §10.3, §10.6 | Single pooled TAA | A2 is mechanically unable on history-dependent, so superiority alone is uninformative | Separate decisions per stratum; Holm correction over 3 tests | — |
| 3 | Persona layer | Frozen | **Optional; off in P for H1–H4** | Proposal abstract, §O1, §5, §6.2, §7.1, §7.2, §10.1, §10.2 suite D, §10.4 (A10), §10.7, §11, §15; checklist §0 | Essential | H1–H4 do not require it; minimal consolidation is the essential derived-artifact source for H4 | Allows shipping engine and application without a trait inference layer | — |
| 4 | H4 inference level | Frozen | **Scenario-level Clopper–Pearson** as primary summary; event-level and per-channel as diagnostics | Proposal §4.1, §10.2 suite D, §10.3 PDLR, §10.6, §13, §16, abstract | Event-level primary | Events in a scenario share a person, history, canaries | Narrower bound (0.168 at 20 scenarios, 12 events each) than the independence-assuming event-level (0.015); illustrated in `cp_bounds.output.txt` | — |
| 5 | H4 "Supported" rule | Frozen | **Zero in-boundary leaks for P AND bound ≤ u\* AND positive control (P-nolineage, A8) detected in ≥ declared share of scenarios** | Proposal §4.1, §10.6 "What the H4 bound means", §16 outcome 5, §13 risk row; checklist §0 and §1 | Zero leaks alone | Mutually exclusive outcomes; zero + bound > u* is Inconclusive, not Supported | u* and share remain Open until pilot | Pilot + supervisor |
| 6 | Archival precondition | Frozen | **(S) Superseded OR (C) Active derived replacement with `MEMORY_DERIVATION` link; branch (A) "valid-ended" removed** | Proposal §7.4 R5, §7.5, §7.6 step 1, §8 invariant 10; checklist §0 "Historical-memory archival bug fixed" | V3 three-branch precondition | Fixes historical-memory bug (silent loss on historical-as-of) | Transactional enforcement; T1–T5 planned | Pilot (harness build; see item 17) |
| 7 | 41-entry reference list | Frozen | **Byte-identical to V3 and V2** | Enforced by `validation/mechanical_checks.py` | — | V4 is a targeted patch | Any reference change must be a V5 patch with its own fact-check audit | — |
| 8 | Minimal consolidation | Frozen | **Essential; retention scoring and persona summaries NOT essential** | Checklist §0; proposal §7.1 ("Minimal consolidation remains essential") | Everything optional | H4 needs derived artifacts (otherwise there is nothing to trace) | A10 remains the only path to persona-trait consolidation | — |
| 9 | Supervisor confirmation of H1 re-scope | Open | Pending | Checklist §6 item 1 | Combined contrast P − P-flat | — | Narrows claim to "validity + history" if combined is chosen | Supervisor |
| 10 | δ_pres (H1 present-state non-inferiority margin) | Open | Not set | Checklist §4, §6 item 2 | — | Set from pilot on development split | §4.1 present-state decision rule depends on it | Pilot |
| 11 | δ (LUAR non-inferiority margin for H3) | Open | Not set | Checklist §4, §6 item 2 | — | Set from pilot | §4.1 H3 decision rule | Pilot |
| 12 | u\* (H4 informativeness limit) | Open | Not set | Checklist §4, §6 items 2, 16 | 0.20 / 0.15 / 0.10 / 0.05 → requires 17 / 23 / 36 / 72 scenarios (per `cp_bounds.output.txt`) | Pilot determines affordable scenario count | §4.1 "Supported" rule, §10.6 bound | Pilot |
| 13 | Positive-control detection share (H4) | Open | Not set | Checklist §4, §6 items 2, 16 | — | Pilot | §4.1 "Supported" rule | Pilot |
| 14 | Suite T stratum mix | Open | Not set | Checklist §4, §6 item 2 | — | Pilot | §10.6 bootstrap stratum weights | Pilot |
| 15 | Implement A2b | Open | Decision needed | Checklist §6 item 3 | Omit | Only ablation that isolates recorded time | Needed for audit-as-of credit; expected small on TAA | Supervisor |
| 16 | One deletion event per scenario in suite D | Open | Decision needed | Checklist §6 item 4 | Many events per scenario | Scenario-level bound tighter per event | Simpler analysis if "yes"; richer per-channel diagnostics if "no" | — |
| 17 | Persona layer implementation | Open | Decision needed | Checklist §6 item 5 | Skip | Confirm "PDT" is acceptable without persona | — | Supervisor |
| 18 | Exclusion constraint feasibility on full schema under concurrent writers | Open | Pilot | Checklist §6 item 6 | Serialisable isolation in the supersession procedure | Reduced-schema SQL passed; full schema untested | Fallback adds latency | Pilot |
| 19 | Ethics determination for optional usability study | Open | Supervisor | Checklist §6 item 7 | Skip study | §10.9 required confirmation before any recruitment | — | Supervisor + ethics |
| 20 | LLM budget / hosted vs local model / LongMemEval subset | Open | Supervisor | Checklist §6 item 8 | Full set | Determines H2 scale | H2 power depends on it | Supervisor |
| 21 | Include Graphiti (B4) | Open | Conditional | Checklist §6 item 9 | Exclude | Closest bi-temporal system; Apache-2.0 | Extra service + LLM cost at ingestion | Pilot |
| 22 | Judge agreement criterion; scenarios per suite | Open | Pilot | Checklist §6 item 10 | — | Set from judge validation | §10.5 and §10.6 depend on it | Pilot |
| 23 | Script-first synthetic suites as primary evidence for H1, H3, H4 | Open | Supervisor | Checklist §6 item 11 | Only public benchmarks | Public benchmarks lack deletion/injection/isolation tasks | Transparency requirement to release suites | Supervisor |
| 24 | Release of injection suite | Open | Supervisor | Checklist §6 item 12 | Private | Dual-use consideration | Reproducibility vs misuse | Supervisor |
| 25 | PostgreSQL version | Open | 14, 16, or 17/18 | Checklist §6 item 13 | — | Version 16 was used in the V2 SQL run; versions 14 – 18 supported on 16 Oct 2026 | Determines pgvector / `btree_gist` behaviour | — |
| 26 | Unverified source items (parts of [20]–[29] not read; [5], [16], [30]–[37] beyond abstracts) | Open | Supervisor | Checklist §6 item 14; ledger §D | — | Not required for V4 claims; any new claim depending on an unread section needs reading | — | — |
| 27 | When and how to execute T1–T5 (archival-precondition harness) | Open | Pilot | Checklist §6 item 17; changelog "Unresolved decisions" item 17 | SQL-only harness | Needs transactional archival procedure, `MEMORY_DERIVATION`, derivation closure (reduced schema does not cover any of these) | Blocks closing invariant 10 | Supervisor + pilot |

Items 1–8 above are **frozen V4 decisions and must not be silently changed**. Items 9–27 remain Open and must be resolved in P1 before P10 can run.

---

## F. Evaluation plan

The authoritative source is the proposal §4, §4.1, §10. This table maps RQ → H → mechanism → baseline/ablation → dataset/split → endpoint → unit → statistic → control → decision rule → report. No new success thresholds are invented.

| RQ | Hypothesis | Mechanism tested | Baseline / ablation | Dataset / split | Primary endpoint | Experimental unit | Statistic | Controls / failure conditions | Decision rule (§4.1) | Report artifact |
|---|---|---|---|---|---|---|---|---|---|---|
| RQ1 | **H1** (two co-primary strata) | Retained bi-temporal version history beyond valid-time filtering | P vs **A2** (primary); A1 (P-flat) and A2b (valid-time-only history) diagnostic | Suite T, test split; **not** LongMemEval or LoCoMo for H1 | TAA per stratum: history-dependent (superiority) + present-state (non-inferiority at δ_pres) | Scenario (cluster) per stratum | Cluster bootstrap CI; sign-flip permutation on per-cluster differences; Holm over 3 tests (non-inferiority combined by intersection-union) | Floor B0; stable and old-value as-of control questions; §4.1 §A.4 "mechanically guaranteed" audit | Supported if history-dependent interval entirely > 0 AND present-state interval entirely > −δ_pres; Not supported if either interval entirely < 0 / < −δ_pres; else Inconclusive | H1 report (two strata + diagnostics A1, A2b, P-flat) |
| RQ2 | **H2** | Hybrid dense + lexical retrieval under temporal predicate | P vs P-dense (A5), P vs P-lexical (A6); A7 fusion variants exploratory | **LongMemEval_S** answerable (excluding assistant-evidence), test split | Evidence Recall@k | Question (cluster) | Intersection-union (larger of two p's); Holm | Both contrasts must favour P | Supported if both intervals > 0; Not supported if either ≤ 0 | H2 report (Recall@k, MRR, nDCG) |
| RQ3 | **H3** co-primary | Validation gate | P vs **P-nogate (A3)** | Suite G, test split; held-out attack families | **ASR AND LUAR** (co-primary) | Scenario (cluster) | Paired test with Holm for ASR; non-inferiority at δ for LUAR | Accept-all and reject-all reference points; A4 sub-check ablation | Supported if ASR reduced AND LUAR non-inferior at δ; Not supported if ASR not reduced OR LUAR loss > δ | H3 report (ASR, LUAR, gate-decision breakdown) |
| RQ4 | **H4** | Lineage-tracked deletion | P with **A8 (P-nolineage)** as **positive control** | Suite D, test split; canaries including a superseded fact | Scenario-level PDLR (scenario with at least one leak on any in-boundary channel) | Scenario (cluster) | **Clopper–Pearson exact at scenario level** (per §10.6 and `cp_bounds.output.txt`); no hypothesis test; u\* declared before freeze | Never-stated control probes; re-learning cases reported separately; positive control (P-nolineage) must detect in ≥ declared share | Supported if 0 in-boundary leaks AND bound ≤ u\* AND positive control detected ≥ share; Not supported if any confirmed in-boundary leak; Inconclusive if no-leak but any of the three conjuncts fails or another defined precondition unmet | H4 report (observed count + exact bound + per-channel diagnostic + positive-control sensitivity) |

**Leakage prevention.** Dev vs test split by scenario-id hash (own suites) and by the benchmark's own split (public); freeze procedure in §10.7; test split touched **once** per configuration; pilots run on development split only.

**Baseline equivalence.** The harness in P3 forces identical scorer, judge, retrieval k, token budgets, and prompt scaffolding across configurations where the ablation does not explicitly alter them (§10.1).

**Reporting negative / inconclusive results.** Report the decision ("Not supported" / "Inconclusive") with the same confidence intervals and the same error analysis as a "Supported" decision. Do not pool results across hypotheses to find an "overall supported".

---

## G. Testing strategy

Matrix of required and optional tests. "Release-blocking?" means mandatory before any public claim.

| Category | Example tests | Prerequisites | Expected evidence | Release-blocking? |
|---|---|---|---|---|
| Unit tests | Predicate parsers; score functions; adapter transforms; config loader | Code in P3/P4 | `pytest` green | Yes |
| Temporal predicates and version-transition tests | Current-state, historical-as-of (point and window), audit-as-of; R1–R6 lifecycle | P4 schema | SQL/Python assertions reproduce the §7.4 worked example (the V2 SQL already covers the reduced-schema version) | Yes |
| **T1 – T5 archival-precondition regression tests** (invariant 10) | T1 reject on currently-valid without replacement; **T2 reject on historical current belief without replacement (V3-bug regression)**; T3 accept on superseded via (S); T4 accept on (C) with provenance intact; T5 deletion closure removes derived replacement | P4 full schema + `MEMORY_DERIVATION` + transactional archival procedure | Transactional procedure returns `VALIDATION_ERROR archival_precondition_unmet` on T1/T2; audit-as-of behaviour per spec; a never-stated probe never answers after T5 | **Yes** (gates invariant 10) |
| Derived-artifact lineage tests | `MEMORY_DERIVATION` traversal; cycle detection; multi-step derivation | P5, P6 | Lineage query returns the expected chain | Yes |
| Deletion-closure tests | §7.7 in-boundary channel sweep (tables, index text, queue payload, cache entries) | P7 | Zero residual retrievable copy per channel | Yes |
| Cross-user isolation and access-control tests | Per-user partition; RLS second layer with BYPASSRLS caveats; cross-user probe | P4, P8 | No user A content returned to user B | Yes |
| Queue idempotency and retry tests | Same event re-enqueued N times | P8 | Exactly one belief transition | Yes |
| Concurrency and transaction tests | Two writers supersede the same row; replay-clock ties (R3) | P4, P8 | No invariant violation; strictly increasing recorded-from | Yes |
| Retrieval correctness tests | Dense exact-per-user; approximate index recall; RRF stability | P6 | Recall ≥ declared floor; RRF deterministic given input lists | Yes |
| Benchmark adapter tests | Official scorer parity; split integrity; data hash | P2 | Oracle counts match the ledger §C row; scorer reproduces trivial scores | Yes |
| End-to-end tests | Full flow user→gate→version→retrieve→answer | P4–P8 | Smoke scenarios pass | Yes |
| Performance and latency tests | §9 fast-path latency; slow-path visibility lag; cost per 1k tokens | P8 | Values recorded descriptively (no target claim) | No (descriptive) |
| Reproducibility checks | Mechanical consistency script (already green); seed-stability for stats | Harness | `mechanical_checks.output.txt` final line = `ALL HARD CHECKS PASSED`; same decision across seeds | Yes |

**Blocked tests at the end of the finalization session:** every row above whose "Prerequisites" include P4 or later is **Blocked** on implementation. The mechanical consistency check and the C-P arithmetic are the only release-blocking items currently green.

---

## H. Application implementation and integration

The PDT application wraps the engine without substituting for engine evaluation.

- **Engine ↔ application interface:** typed API with `remember(event) → candidate_id`, `ask(question, t, β) → answer`, `inspect(memory_id)`, `correct(memory_id, replacement)`, `delete(memory_id or key)` returning deletion-log ids; a stable schema for provenance and derivation metadata; versioned clients.
- **Memory inspection and provenance display:** inspector tab shows per-memory version history (both axes), evidence rows, derivation links; "why is this here?" expands evidence and gate decisions.
- **Correction and update flow:** user asserts a replacement; the gate labels it `user-authored evidence`; the supersession procedure runs; the UI shows the before/after versions side by side.
- **Historical questions:** historical-as-of query predicate exposed as "what did you believe about X on date Y?" with a date picker; audit-as-of exposed in an auditor view with a (t, β) picker.
- **User-controlled deletion:** confirmation panel lists what will be removed (counts per in-boundary channel from §7.7); deletion returns a receipt; a canary probe shows the deleted key no longer answers.
- **Uncertainty communication:** inferred items (persona traits, consolidated semantic memories) are visually marked with "inferred" badges and expand to their derivation chain; low-confidence extractions are labelled.
- **Error handling and observability:** structured error codes; a dashboard with queue lag, visibility lag, gate acceptance rate, deletion backlog, invariant-check counts.
- **Privacy and security boundaries:** cross-user isolation per §7.8; §14 ethics text respected; no content or hash in audit; sign-out revokes session, retention schedule for backups documented.
- **Persona layer:** off by default; if enabled, every trait is marked as an inference with evidence links and is re-evaluated when any source is deleted (R6).

Acceptance criteria for each flow are listed in §G and §J.

---

## I. Risks, dependencies, fallbacks

Augments the proposal §13 risk register with execution and verification risks.

| # | Risk | Probability | Impact | Early-warning signal | Mitigation | Fallback | Affected phase/hypothesis | Evidence needed to close |
|---|---|---|---|---|---|---|---|---|
| R1 | Benchmark data changes after freeze (LongMemEval / LoCoMo updates) | Medium | High | Hash mismatch at smoke test | Pin by commit hash + mirror | Fall back to the declared subset | P2, H2 | Smoke-test hash log |
| R2 | API model deprecated or non-deterministic | Medium | Medium | Scorer drift between runs | Pin model id, temperature, seed; capture env | Switch to pinned alternative, record in manifest | P3, all H | Reproducibility test result |
| R3 | Unverified source claim later found false | Low–Medium | Medium | Peer feedback; publication of correction | Depth-labelled ledger per source | Remove claim or re-cite; update audit and changelog | §6, §10 | Updated audit row |
| R4 | Insufficient statistical power (too few clusters) | Medium | High | Pilot confidence intervals too wide | Enlarge suites D and T; cheaper P1 decisions on scope | Narrow claim to the strata that are powered; mark others Inconclusive | H1, H3, H4 | Pilot CI widths |
| R5 | Extractor unreliable; gate miscalibrated | Medium | High | H3 LUAR excessive loss in pilot | Calibration (§7.5); conservative accept threshold | Human-in-loop queue (optional §11) | H3 | Gate-decision breakdown |
| R6 | Temporal inconsistency (invariant 4 violation under concurrency) | Low–Medium | High | Exclusion-constraint failures in logs | Transactional supersession; serialisable fallback | Serialisable isolation; publish perf cost | P4, P8 | Concurrency-test report |
| R7 | Incomplete deletion / residual leakage | Medium | Very high | Positive-control A8 detects; residual appears on an in-boundary channel | §7.7 closure; per-user vector partitions | Degrade H4 to "Not supported" and investigate | P7, H4 | Suite D output |
| R8 | Performance below usable threshold | Medium | Medium | §9 latency measurements regress | Pgvector tuning; batching; caching with deletion propagation | Document and ship with the limitation | P8 | Latency log |
| R9 | Cannot reproduce own results | Low | Very high | Rerun produces different decisions | Pinned env, deterministic seeds, run manifests | Freeze with a stable seed; publish seeds | P10, P13 | Reproducibility rerun |
| R10 | T1 – T5 harness still blocked at the end of P9 | Medium | Very high | No merge of harness in `implementation/tests/archival/` | Prioritise invariant 10 harness in P4/P5; supervisor sign-off on item 17 | Degrade invariant 10 claim to specification-only in the final report | H1-adjacent; invariant 10 | T1 – T5 outputs |
| R11 | Judge agreement fails perturbation check | Medium | Medium | Validation per §10.5 fails | Use the smaller trusted judge; shrink claims to annotated subsets | Human adjudication on contested items | H2, H3 | Judge-validation report |
| R12 | Mem0 or Graphiti behaviour changes between pinning and release | Medium | Low | Repo HEAD drifts from pinned commit | Pinned commits with SHA; mirror | Report at pinned commit only | P3, baselines | Commit SHA in manifest |

---

## J. Final acceptance and delivery

Separate **mandatory** from **optional**. The project is not called "complete" until every mandatory row is satisfied.

| Row | Deliverable | Mandatory? | Acceptance criterion |
|---|---|---|---|
| J1 | Research specification (V4) | Mandatory | Mechanical checks pass; all §4.1 decision rules frozen; §10.7 freeze list populated |
| J2 | Working, tested memory engine | Mandatory | All invariants 1–10 enforced; every "Yes" row in §G Tested; T1–T5 Tested |
| J3 | Reproducible baselines and evaluation runner | Mandatory | Smoke evaluation reproducible from a fresh checkout and a pinned env |
| J4 | Executed experiments (H1–H4) with statistical reports | Mandatory | Each hypothesis has a decision + CI + diagnostics + error analysis |
| J5 | Error analysis and limitations | Mandatory | At minimum one page per RQ; honest "Not supported" or "Inconclusive" reports where applicable |
| J6 | Personal Digital Twin demonstration | Mandatory for the application track; **optional** for the research track | All §11 flows pass their acceptance tests |
| J7 | Security and deletion test evidence | Mandatory | §G cross-user, deletion-closure, T1–T5 outputs attached |
| J8 | Source and dataset attribution | Mandatory | All licenses and manifests in `research/benchmarks/` and `research/sources/` |
| J9 | Reproducibility instructions | Mandatory | `README.md` run-your-own section + pinned env |
| J10 | Clean documentation and repository | Mandatory | Canonical version-free Lab documents and README navigation; retained evidence under `research/sources/` and `validation/`; documentation inventory in `review/EXISTING_WORK_IMPACT_ANALYSIS.md` current |
| J11 | Final report + presentation | Mandatory | Reports every decision (Supported / Not supported / Inconclusive) with its evidence |
| J12 | Persona layer | **Optional** | If enabled: A10 report present and derivation lineage traceable |
| J13 | Public release of own suites | Optional (per open decision 24) | Licence declared; dual-use review of suite G |

**If a mandatory research result is not achieved:** report it honestly per §J5, following the proposal §4.1 decision rules. Do not hide a "Not supported" or "Inconclusive" result; mark it in the final report and the public release.

---

## K. Research checkpoint view

This is a coarse research-phase checkpoint view, not a second RL task system. Exact task edges and preparation vs phase-acceptance gates are in PLAN/TASK_GRAPH.md; actual acceptance evidence/status is in PLAN/PROJECT_STATUS.md. RL-001 is Done by owner-confirmed team approval; downstream execution remains gated by each task's accepted prerequisites, supervisor/ethics decisions, publication permission and experimental freeze as applicable. The finalization script described in §D.0 is absent and cannot be revalidated here.

Statuses: `Not Started`, `In Progress`, `Blocked`, `Implemented—Untested`, `Tested`, `Complete`.

| Phase | Task ID | Description | Status | Dependencies | Acceptance | Evidence / artifact path | Blocking issue | Next action |
|---|---|---|---|---|---|---|---|---|
| P0 | P0.1 | Fix stale check label and rerun mechanical checks | Complete | — | Final line `ALL HARD CHECKS PASSED` | `validation/mechanical_checks.output.txt` | — | — |
| P0 | P0.2 | Re-run preservation of V1/V2/V3/original | Complete | P0.1 | `PRESERVED` | earlier preservation report (no longer retained; see `CHANGELOG.md`) | — | — |
| P0 | P0.3 | Re-inspect V2 SQL log | Complete | — | 43 PASS, MD5 match | `validation/bitemporal_log_inspection.txt` | — | — |
| P0 | P0.4 | Historical finalization layout | Complete (historical) | — | Version-free Lab set/evidence retained; obsolete symlink dirs absent | Lab root, `validation/`, `research/sources/` | Historical finalizer absent | Do not rerun absent finalizer |
| P0 | P0.5 | Standing roadmap/spec/traceability/navigation | Complete (historical baseline) | P0.1–P0.4 | Four standing files exist; earlier INVENTORY/FINAL_REPORT not retained | Lab root; consolidated inventory in review | RL-001 owner-confirmed; RL-002 scaffold acceptance/merge pending | Complete separate scaffold review and merge |
| P1 | P1.1 | Resolve open decisions 1, 3, 5, 7, 9, 10, 11, 12 (supervisor-approvable without pilot) | Not Started | P0 | Decision log populated | `TECHNICAL_SPECIFICATION.md` §2 | — | Meet supervisor with the §E table |
| P1 | P1.2 | Design pilot procedures for δ_pres, δ, u\*, share, stratum mix and qualification | Not Started | RL-010; RL-020/030 non-blocking preparation | Pilot plan dated and approved | `TECHNICAL_SPECIFICATION.md` §2 | — | Draft the pilot design |
| P2 | P2.1 | Pin and mirror LongMemEval, LoCoMo; write adapters | Not Started | RL-002/003 for mirrors; RL-030 for adapters | `MANIFEST.txt` hashes match ledger §C | `research/benchmarks/` | Benchmark access | Start on LongMemEval (MIT licence available) |
| P2 | P2.2 | Write generators for own suites T, D, G | Not Started | P1.1 | Deterministic smoke run | `research/benchmarks/own_*` | — | Start with suite D (smallest) |
| P3 | P3.1 | Build harness skeleton with manifest + stats module | Not Started | RL-003/013; D-20 before paid calls | Smoke run reproducible | `evaluation/` | — | — |
| P3 | P3.2 | Implement baselines B0, B1, B2, B2h, B3 (pinned); B4 conditional | Not Started | P3.1 | Smoke scores on a dummy suite | `evaluation/baselines/` | — | — |
| P3 | P3.3 | Judge harness and perturbation calibration | Not Started | RL-030/020/011 | Validation per §10.5 passes | `evaluation/judge/` | — | — |
| P4 | P4.1 | Full-schema DDL + migrations | Not Started | P1.1, PostgreSQL version decision | Schema diff matches §8 | `implementation/schema/`, `implementation/migrations/` | — | — |
| P4 | P4.2 | Transactional supersession, archival, deletion procedures | Not Started | P4.1 | Procedure returns `VALIDATION_ERROR archival_precondition_unmet` on T1/T2 setups | `implementation/engine/` | — | — |
| P4 | P4.3 | **T1 – T5** regression harness | **Blocked** | RL-010/040/041/042/061/070 (D-27 + real T5 deletion) | Each of T1–T5 passes per §G | `implementation/tests/archival/` | Harness not written; open decision 17 | Approve item 17; start harness |
| P5 | P5.1 | Validation gate + assertion-mode classifier | Not Started | P4 | Gate reason codes logged | `implementation/engine/gate/` | — | — |
| P6 | P6.1 | Dense + lexical + RRF retrieval | Not Started | RL-043/051 | Recall@k reproducible | `implementation/engine/retrieval/` | pgvector install | — |
| P6 | P6.2 | Minimal consolidation + derivation links | Not Started | RL-040/042 | Lineage query returns expected chain | `implementation/engine/consolidation/` | — | — |
| P7 | P7.1 | Deletion closure in-boundary sweep | Not Started | P4, P5, P6 | Zero residual on every in-boundary channel | `implementation/engine/deletion/` | — | — |
| P8 | P8.1 | Fast + slow paths; job queue with `SKIP LOCKED` | Not Started | RL-041/050/060/070 | Idempotency test passes | `implementation/engine/paths/` | — | — |
| P9 | P9.1 | Full §G matrix green or waived | Not Started | P4–P8 | §G mandatory rows Tested | `tests/report.md` | — | — |
| P10 | P10.1 | Pilot on development split; freeze values | Not Started | P2, P3, P4–P9 | Freeze list signed | `evaluation/runs/pilot/` | — | — |
| P10 | P10.2 | Run H1 experiment | Not Started | P10.1 | Decision per §4.1 | `evaluation/runs/<ts>/H1/` | — | — |
| P10 | P10.3 | Run H2 experiment | Not Started | P10.1 | Decision per §4.1 | `evaluation/runs/<ts>/H2/` | — | — |
| P10 | P10.4 | Run H3 experiment | Not Started | P10.1 | Decision per §4.1 | `evaluation/runs/<ts>/H3/` | — | — |
| P10 | P10.5 | Run H4 experiment | Not Started | P10.1, P7 | Decision per §4.1 | `evaluation/runs/<ts>/H4/` | — | — |
| P11 | P11.1 | Statistical reports + error analysis | Not Started | P10 | Reports attached | `reports/` | — | — |
| P12 | P12.1 | PDT application user flows | Not Started | P4–P8 | §G app rows Tested | `application/` | — | — |
| P13 | P13.1 | Release package (schema, code, suites, configs, docs) | Not Started | P0–P12 as available | Reviewer rerun succeeds | `release/vX.Y.Z/` | — | — |
| P13 | P13.2 | Final report + defense deck | Not Started | P10, P11 | Submitted | `release/vX.Y.Z/report/`, `release/vX.Y.Z/defense/` | — | — |

No individual team members, personal assignments, calendar deadlines, or week-by-week schedule appear in this tracker — ordering is by prerequisites only, consistent with §15 of the proposal.
