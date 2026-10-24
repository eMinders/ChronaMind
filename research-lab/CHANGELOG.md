# Changelog — Research Lab (`research-lab/`)

This file records the revision history of the Research Lab specification (`Graduation_Project_Proposal.md`, `Methodology_and_Consistency_Checklist.md`, `TECHNICAL_SPECIFICATION.md`, `PROJECT_ROADMAP.md`) and its supporting reference corpus (`reference/`).

---

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
