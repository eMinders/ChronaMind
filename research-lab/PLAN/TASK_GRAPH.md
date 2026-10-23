# TASK_GRAPH.md — Research Lab actionable task breakdown

**Status:** RL-001 Done by owner-confirmed team approval, recorded in PROJECT_STATUS.md §6. Planning is ready for controlled distribution; RL-002 separate acceptance/merge and downstream gates remain pending. Canonical engineering contracts: [ARCHITECTURE.md](ARCHITECTURE.md); owner/S-07 record: [review decisions](../review/FEASIBILITY_REVIEW_DECISIONS.md).
**Source of truth:** derived from [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §C–§K and [`../Methodology_and_Consistency_Checklist.md`](../Methodology_and_Consistency_Checklist.md) §6.
**Companion documents:** [`DEPENDENCY_MAP.md`](DEPENDENCY_MAP.md), [`ASSIGNMENTS.md`](ASSIGNMENTS.md), [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md), [`SCHEDULE.md`](SCHEDULE.md).

**How to read this document.** Section A below defines **phase-level final deliverables** (`D-P0` through `D-P13`) — each phase's accepted output. Section B defines the **work packages** (`RL-NNN`) that collectively produce those deliverables. A phase is accepted only when its final deliverable passes the acceptance criteria in Section A.

**Convention.** Task IDs are `RL-NNN`, grouped by series:

- `RL-00x` — Research Lab foundation and tooling.
- `RL-01x` — Phase P1 open decisions (research freezes).
- `RL-02x` — Phase P2 benchmarks and own suites.
- `RL-03x` — Phase P3 harness, baselines, judge, statistics.
- `RL-04x` — Phase P4 schema and engine core.
- `RL-05x` — Phase P5 validation gate and lifecycle.
- `RL-06x` — Phase P6 retrieval, consolidation, lineage.
- `RL-07x` — Phase P7 deletion and privacy.
- `RL-08x` — Phase P8 integration and asynchronous paths.
- `RL-09x` — Phase P9 testing matrix (including T1–T5 regression).
- `RL-10x` — Phase P10 pilot and controlled experiments.
- `RL-11x` — Phase P11 statistical analysis and error analysis.
- `RL-12x` — Phase P12 PDT demonstration.
- `RL-13x` — Phase P13 release and defence.

**Dependency classes used in this document** (per task spec):

- **Blocking:** cannot start until the prerequisite is accepted.
- **Non-blocking:** may start early; cannot complete until the prerequisite completes.
- **Independent:** no prerequisite.
- **Review:** implementation done; completion gated on review.
- **Decision:** blocked on an Open decision in [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md).

**Status key:** `Proposed`, `Ready`, `In Progress`, `Blocked`, `In Review`, `Done`.

Current task status is canonical in PROJECT_STATUS.md §10: RL-001 Done; RL-002 In Review; RL-010/012/013/014/015/016 Ready for decision preparation; RL-003 and RL-020/021 Blocked by explicit prerequisites; other tasks follow their listed prerequisites. The six first-wave GitHub issues RL-010/012/013/014/015/016 are published and linked from PROJECT_STATUS.md §10; no other task issue is asserted published. The global RL-001 gate is satisfied (not repeated in every technical dependency list). Phase tables govern final deliverable acceptance, not all preparatory work permitted by task edges. `Dependencies` is canonical; `Blocks` lists are non-exhaustive handoff examples, not a second edge source. The map contains all explicit RL edges; decision conditions are in DECISIONS_REGISTER.md. Pre-publication bootstrap claims follow CONTRIBUTING.md §3 using the owner-confirmed RL-001 acceptance; no contributor claim is inferred. Each task requires scope, deliverable, acceptance and independent review; full/compact drafts supplement the short contracts below, not replace their prerequisites.

---

## Section A — Phase-level final deliverables

Each phase ends when its final deliverable `D-PN` passes the acceptance criteria below. These deliverables are what the next phase consumes; a phase is **not** complete until the handoff artifact exists and has been reviewed.

### A.0 D-P0 — Approved initialisation package (In Review; RL-002 acceptance pending)

- **Objective:** a validated, independent Research Lab working tree with its planning package reviewed.
- **Final deliverable:** the Lab tree under `research-lab/`, with the ten top-level planning documents, the issue drafts, and the mechanical check output quoted in `PROJECT_STATUS.md §8`.
- **Acceptance criteria:**
  - `python3 validation/mechanical_checks.py ...` ends with `SUMMARY: ALL HARD CHECKS PASSED` (verified).
  - All ten top-level planning files listed in [`ARCHITECTURE.md`](ARCHITECTURE.md) §2 exist and resolve their internal cross-references.
  - `RL-001` approved by the team (satisfied by owner confirmation).
  - RL-002 separately accepted/merged under its task contract; not evidenced yet.
- **Work packages:** `RL-001` (approval gate), `RL-002` (scaffold).
- **Dependencies:** none upstream; downstream deliverables depend on `RL-001` being accepted.
- **Parallel work:** RL-002 scaffold acceptance/merge can proceed alongside independent P1 decisions; RL-001 no longer blocks them.
- **Integration/review:** package approval owner-confirmed; separate scaffold review/merge remains pending.
- **Next-phase handoff:** RL-001 owner-confirmation record; missing per-member identities/review hashes are audit limitations, not a reopened gate. Record any separate RL-006 publication authorization explicitly; package acceptance alone is not publication permission.

### A.1 D-P1 — Frozen decision log + approved pilot plan

- **Objective:** turn every roadmap §E Open row into a Frozen row (or a dated Pilot plan with owner, target, and criterion).
- **Final deliverable:** a decision log at `DECISIONS_REGISTER.md`, with every Open row either Frozen or Pilot-scheduled; plus `research-lab/PLAN/pilot_plan.md`.
- **Acceptance criteria:**
  - Every roadmap §E item 9–27 has a Frozen row or a Pilot row with owner + target date + pass criterion.
  - No §4.1 decision rule still references an undeclared value.
  - `TECHNICAL_SPECIFICATION.md §2` reflects the same decisions.
  - Mechanical check still passes.
- **Work packages:** `RL-010`, `RL-011`, `RL-012`, `RL-013`, `RL-014`, `RL-015`, `RL-016`.
- **Dependencies:** `D-P0`.
- **Parallel work:** independent decision clusters can prepare concurrently; RL-011 follows RL-010. Infrastructure RL-003 waits for runtime RL-013 and scaffold; RL-005 also waits for PG target RL-012. These are branches, not a flat parallel batch.
- **Integration/review:** phase coordinator (Member 6) collates an end-of-phase decision sweep that each decision owner signs.
- **Next-phase handoff:** `pilot_plan.md` listing every pilot-dependent value with the owner and the smoke-size needed; the frozen Lab language/PG version so `RL-040` can start.

### A.2 D-P2 — Benchmark and own-suite freeze bundle

- **Objective:** make LongMemEval_S, LoCoMo, and the three own suites (T, D, G) reproducibly available with licence, split freeze, and deterministic generators.
- **Final deliverable:** `research/benchmarks/` with `longmemeval/`, `locomo/`, `own_T/`, `own_D/`, `own_G/`, `splits/` subdirectories, each containing `MANIFEST.txt`, licence file, `README.md`, and generators/adapters.
- **Acceptance criteria:**
  - MD5/SHA-256 hashes match ledger §C re-count.
  - Each own-suite generator is deterministic given a seed; a smoke run matches expected scenario / event / canary counts.
  - Adapters run the official scorers and reproduce trivial `B0` metrics to within rounding (verified in P3 once harness exists; recorded here as a cross-reference).
  - Split freeze list signed by the P2 coordinator; no scenario appears in both dev and test splits.
- **Work packages:** `RL-020`, `RL-021`, `RL-022`, `RL-023`, `RL-024`, `RL-025`, `RL-026`.
- **Dependencies:** `D-P1` (needs scope sign-off `RL-010` for own-suite release decision `RL-016`; needs `RL-014` for suite D event cardinality).
- **Parallel work:** `RL-020` (LongMemEval, Member 4) and `RL-021` (LoCoMo, Member 1) run concurrently with the three own-suite generators `RL-022/023/024` (Member 3). `RL-025` (adapters, Member 4) can begin once `RL-020/021` and the `RL-030` scorer interface are accepted. `RL-026` is the last-mile freeze.
- **Integration/review:** phase coordinator (Member 3) runs a cross-check that each `MANIFEST.txt` hash matches the ledger and that the smoke adapter output is reproducible.
- **Next-phase handoff:** frozen splits usable by any configuration; manifests and licence files that P3 harness smoke-tests consume.

### A.3 D-P3 — Working evaluation harness with baselines, judge, statistics

- **Objective:** a shared harness so every configuration (P, A1–A10, A2b, B0–B4) runs under one scorer, one judge, one statistics module.
- **Final deliverable:** `evaluation/` with `harness/`, `baselines/`, `judge/`, `stats/`, `runs/pilot/` subdirectories; smoke run reproducible on a repeat seed.
- **Acceptance criteria:**
  - Running any baseline on a dummy suite produces identical scores on repeat (same seed, same config hash).
  - Judge validation per proposal §10.5 passes against the pilot-set criterion from `RL-011`.
  - Statistics module reproduces `validation/cp_bounds.output.txt` byte-identically for the C–P case; unit tests pass per statistic.
  - All baselines record pinned commit SHAs in the manifest.
- **Work packages:** `RL-030`, `RL-031`, `RL-032` (conditional), `RL-033`, `RL-034`.
- **Dependencies:** required P1 decisions (`RL-013` language; `RL-011` pilot design); final pilot-dependent values freeze in RL-100, not before harness construction.
- **Parallel work:** Member 5 owns `RL-030`, `RL-031`, `RL-032`, `RL-033`; Member 1 owns `RL-034`. Independent at module level once `RL-030` interface is agreed. Member 2 reviews.
- **Integration/review:** phase coordinator (Member 5) runs a dummy-suite smoke across every baseline + judge + stats to confirm the manifests are consistent.
- **Next-phase handoff:** harness interface that `RL-025` adapters already target; statistics module that `RL-110` consumes.

### A.4 D-P4 — Full engine schema + procedures + retrieval predicates

- **Objective:** the full data model and transactional procedures of proposal §7–§8, with every invariant 1–10 enforced somewhere, so P5–P8 can build on it.
- **Final deliverable:** `implementation/schema/`, `implementation/migrations/`, `implementation/engine/` with procedures and retrieval predicates; `implementation/tests/schema/` smoke green.
- **Acceptance criteria:**
  - Full-schema SQL runs cleanly against the pinned PG version.
  - Reduced-schema V2 SQL assertions still pass against the full schema (43 PASS lines + `ALL ASSERTIONS PASSED`).
  - `invariants_matrix.md` lists an enforcement site for each invariant 1–10.
  - Transactional archival procedure returns `VALIDATION_ERROR archival_precondition_unmet` on T1/T2 setups.
  - Retrieval predicates reproduce the §7.4 worked example on the full schema.
  - Up→down→up restores logical schema/data/constraint equivalence to a fresh up; physical database bytes are not the criterion.
- **Work packages:** `RL-040` (DDL, Member 4), `RL-041` (migrations, Member 4), `RL-042` (procedures, Member 2), `RL-043` (retrieval predicates, Member 2), `RL-061` begins after `RL-040` and `RL-042` are accepted.
- **Dependencies:** `D-P1` (`RL-010` scope frozen; `RL-012` PG version; `RL-013` language).
- **Parallel work:** `RL-040` must land first; then `RL-041`, `RL-042`, `RL-043` run in parallel. Member 1 continues `RL-034` stats work during this phase; Member 5 drafts baseline integration; Member 6 coordinates P6 planning.
- **Integration/review:** phase coordinator (Member 2) runs the schema smoke + the T1/T2 harness scaffolding to confirm invariant 10 enforcement.
- **Next-phase handoff:** stable schema + procedures + retrieval predicate functions; `invariants_matrix.md`; `implementation/tests/schema/` baseline log.

### A.5 D-P5 — Validation gate + conflict detector accepted

- **Objective:** the §7.5 gate and the conflict detector + supersession wiring.
- **Final deliverable:** `implementation/engine/gate/` and the conflict-detector wiring; reason-coded gate decisions logged for every synthetic candidate; property-based idempotency tests pass.
- **Acceptance criteria:**
  - Gate assigns the labelled outcome on synthetic candidates at ≥ the pilot-determined accuracy from `RL-011`.
  - A known contradiction triggers the supersession procedure once and only once.
  - All gate reason codes from proposal §7.5 are present.
- **Work packages:** `RL-050`, `RL-051`.
- **Dependencies:** `D-P4` (needs schema + procedures).
- **Parallel work:** 2 members (M3 gate; M2 reviews). Others continue parallel P4/P6 work: M4 T1–T5 harness scaffolding, M5 baselines, M1 stats, M6 coordinates.
- **Integration/review:** phase coordinator (Member 3) runs gate accuracy + idempotency tests.
- **Next-phase handoff:** gate-decision interface consumed by P6 retrieval and P7 deletion closure.

### A.6 D-P6 — Hybrid retrieval + consolidation + lineage accepted

- **Objective:** §7.6 retrieval (dense + lexical + RRF) and minimal consolidation with `MEMORY_DERIVATION` lineage.
- **Final deliverable:** `implementation/engine/retrieval/` and `implementation/engine/consolidation/`; Recall@k, MRR, snapshot-consistency tests green.
- **Acceptance criteria:**
  - Evidence Recall@k and MRR match harness definitions.
  - Snapshot-consistency test: fast-path reads do not observe partial supersession.
  - Lineage query returns the expected chain from a derived version back to source versions.
- **Work packages:** `RL-060` (Member 5), `RL-061` (Member 2).
- **Dependencies:** `D-P4`, `D-P5`.
- **Parallel work:** Member 3 begins deletion-closure design in parallel; Member 4 concurrency tests; Member 1 release scripts; Member 6 coordinates.
- **Integration/review:** phase coordinator (Member 6) checks that `RL-060` + `RL-061` compose correctly on the harness smoke.
- **Next-phase handoff:** retrieval and lineage functions used by P7, P8, and P12.

### A.7 D-P7 — Deletion closure accepted

- **Objective:** §7.7 in-boundary deletion closure.
- **Final deliverable:** `implementation/engine/deletion/`; T5 passes; §7.7 in-boundary sweep returns zero residual.
- **Acceptance criteria:**
  - T5 passes (end-to-end deletion closure removes the derived replacement).
  - Every in-boundary channel listed in §7.7 shows no residual retrievable copy after a deletion.
  - A never-stated control probe is never answered.
  - Boundary-free audit record created (no content, no hash).
- **Work packages:** `RL-070` (Member 3).
- **Dependencies:** `D-P4`, `D-P5`, `D-P6`.
- **Parallel work:** 1 member; others continue P4–P8 work in parallel.
- **Integration/review:** phase coordinator (Member 3) runs the §7.7 sweep + the T5 scenario.
- **Next-phase handoff:** deletion closure consumed by `RL-093` deletion-closure tests, `RL-105` H4, and `RL-120` PDT deletion flow.

### A.8 D-P8 — Async paths + queue accepted

- **Objective:** §7.1–§7.2 fast/slow paths + PostgreSQL job queue with `FOR UPDATE SKIP LOCKED`, idempotency keys, per-user advisory locks, dead-letter table; plus replay clock.
- **Final deliverable:** `implementation/engine/paths/`; replay clock utility; idempotency test green.
- **Acceptance criteria:**
  - Idempotency test: same event re-enqueued N times produces exactly one belief transition.
  - Visibility lag measured descriptively per §10 O6.
- **Work packages:** `RL-080` (Member 5), `RL-081` (Member 1).
- **Dependencies:** `D-P4`, `D-P5`, `D-P6`, `D-P7`.
- **Parallel work:** Member 4 concurrency tests + T1–T5 harness; Member 3 cross-user tests; Member 2 reviews.
- **Integration/review:** phase coordinator (Member 5) runs the idempotency + replay-clock harness.
- **Next-phase handoff:** async paths and replay clock used by P9 concurrency tests and the pilot run in P10.

### A.9 D-P9 — Full §G testing matrix green or waived

- **Objective:** every row in roadmap §G is either `Tested` or has a documented, approved waiver.
- **Final deliverable:** `tests/report.md` summarising every §G row; `implementation/tests/archival/` logs; `implementation/tests/concurrency/` logs; `implementation/tests/security/` logs.
- **Acceptance criteria:**
  - T1–T5 each pass per §G.
  - Concurrency + replay-clock tests pass (no invariant violation; strictly increasing `recorded_from`).
  - Cross-user + RLS tests pass (no cross-user content leakage).
  - Deletion-closure tests pass (zero residual on every in-boundary channel).
  - Reproducibility: mechanical check `ALL HARD CHECKS PASSED` on the final tree.
- **Work packages:** `RL-090`, `RL-091`, `RL-092`, `RL-093`, `RL-094` (sign-off).
- **Dependencies:** `D-P4`, `D-P5`, `D-P6`, `D-P7`, `D-P8`.
- **Parallel work:** Member 4 (T1–T5 + concurrency + sign-off), Member 3 (cross-user + deletion-closure), Member 5 (end-to-end smoke), Member 2 (reviewer). Member 1 prepares release scripts; Member 6 coordinates handoff.
- **Integration/review:** phase coordinator (Member 4) runs the §G matrix sign-off.
- **Next-phase handoff:** signed `tests/report.md` is the gate for the P10 pilot.

### A.10 D-P10 — Pilot freeze + four experiment results

- **Objective:** signed freeze list and one decision per hypothesis.
- **Final deliverable:** `evaluation/runs/pilot/FREEZE_LIST.md` (signed); `evaluation/runs/<ts>/H1/`, `/H2/`, `/H3/`, `/H4/` with reports + per-cluster JSONL.
- **Acceptance criteria:**
  - Every pilot-dependent value frozen with a dated supervisor sign-off.
  - No experiment used the test split before the freeze list was signed.
  - Each hypothesis has a decision (Supported / Not supported / Inconclusive) derived mechanically from §4.1.
  - Rerun on a different seed produces the same decision (sensitivity analysis).
- **Work packages:** `RL-100` (pilot + freeze, Member 3), `RL-101` (H1, Member 3), `RL-102` (H2, Member 4), `RL-103` (H3, Member 3), `RL-105` (H4, Member 2).
- **Dependencies:** `D-P2`, `D-P3`, `D-P9`.
- **Parallel work:** M3 runs pilot + H1 + H3; M4 runs H2; M2 runs H4. M1 writes stats glue (RL-110 scaffolding); M5 continues PDT; M6 coordinates.
- **Integration/review:** phase coordinator (Member 3) runs the freeze sign-off; peer + supervisor review each experiment report.
- **Next-phase handoff:** per-hypothesis reports and `per_cluster.jsonl` consumed by `RL-110`.

### A.11 D-P11 — Statistical reports + error analysis

- **Objective:** one report per hypothesis + a shared error analysis.
- **Final deliverable:** `reports/H1.md`, `reports/H2.md`, `reports/H3.md`, `reports/H4.md`, `reports/error_analysis.md`.
- **Acceptance criteria:**
  - Each report derives its decision mechanically from §4.1 using the stats module output.
  - Error analysis includes a sample of answered + unanswered items per stratum.
  - Review by Members 2 + 3 + supervisor.
- **Work packages:** `RL-110` co-authored: M3 writes H1 + H3 sections; M4 writes H2 section; M2 writes H4 section; M1 writes stats/error-analysis glue.
- **Dependencies:** `D-P10`.
- **Parallel work:** 4 co-authors; parallel section write-up.
- **Integration/review:** phase coordinator (Member 2) merges the sections and runs a consistency check against the stats module output.
- **Next-phase handoff:** report bundle used by `RL-130` and `RL-131`.

### A.12 D-P12 — PDT demonstration

- **Objective:** demonstrate the engine via the six §11 user flows.
- **Final deliverable:** `application/` with inspector, correction, historical, deletion, uncertainty, observability flows + their acceptance tests.
- **Acceptance criteria:**
  - Each §11 user flow has a passing acceptance test.
  - Cross-user privacy tests pass.
  - If `RL-015` ethics determination enables a user study, the study plan is attached.
- **Work packages:** `RL-120` (Member 5).
- **Dependencies:** `D-P4`, `D-P5`, `D-P6`, `D-P7`, `D-P8` (not `D-P10`; this track runs in parallel with P10/P11).
- **Parallel work:** 1 member (M5), optionally supported by M1 if load allows. Others focus on P10/P11.
- **Integration/review:** phase coordinator (Member 5) runs the §11 flow acceptance tests; Member 1 cross-user privacy sweep.
- **Next-phase handoff:** application bundle used by `RL-130` if the application track is Mandatory.

### A.13 D-P13 — Release package + final report + defence

- **Objective:** reproducible release and the final report.
- **Final deliverable:** `release/vX.Y.Z/` with schema, code, suites, configs, docs, licence files; `reports/final_report.md`; `reports/defence_deck_notes/`.
- **Acceptance criteria:**
  - A reviewer with only the release and a PostgreSQL instance can rerun the smoke evaluation and reproduce the smoke summary (`RL-130-REPRO` validates this).
  - The final report covers every decision (Supported / Not supported / Inconclusive) with its evidence.
  - Mechanical check still passes on the release tree.
- **Work packages:** `RL-130` (release package, Member 1), `RL-130-REPRO` (independent reproduction run, Member 4), `RL-131` (final report + defence deck, Member 6 author with Members 2+3+4 co-authors, Members 1+5+supervisor reviewers).
- **Dependencies:** `D-P9`, `D-P10`, `D-P11`, optionally `D-P12`.
- **Parallel work:** 4 members co-authoring; Member 1 package + Member 4 reproduction run in sequence (reproducer cannot be the author).
- **Integration/review:** phase coordinator (Member 1) runs the release package + the mechanical check; `RL-130-REPRO` is executed by Member 4 as the reviewer independent of the package author.
- **Next-phase handoff:** none — final phase.

---

## Section B — Work packages

Every work package below has a unique ID, phase, parent deliverable (`D-PN`), objective, scope, owner, dependencies, deliverable, and acceptance criteria. Owners come from [`ASSIGNMENTS.md`](ASSIGNMENTS.md) and are tagged with their role shape.

---

## Section 0 — Research Lab foundation (series `RL-00x`)

These tasks initialise the Lab infrastructure. After RL-001 acceptance they start only when their listed prerequisites are accepted; local bootstrap claims follow CONTRIBUTING.md §3.

### RL-001 — Approve Research Lab initialisation package

- **Parent deliverable:** `D-P0`.
- **Owner (proposed):** team review (all six members); phase coordinator Member 6 collates comments.
- **Dependencies:** none (approval gate).
- **Status:** Done — owner-confirmed team approval; see PROJECT_STATUS.md §6.
- **Scope:** review [`ARCHITECTURE.md`](ARCHITECTURE.md), [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md), this task graph, [`ASSIGNMENTS.md`](ASSIGNMENTS.md), [`DEPENDENCY_MAP.md`](DEPENDENCY_MAP.md), [`SCHEDULE.md`](SCHEDULE.md), [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md), [`PROJECT_STATUS.md`](PROJECT_STATUS.md), issue templates, PR template, and the issue drafts under `PLAN/issue_drafts/`. Request changes or approve.
- **Deliverables:** written approval (comment on the eventual tracking issue once created, or a note in `PROJECT_STATUS.md`).
- **Acceptance:** owner confirms that the team completed RL-001 review/approval; accepted in this pass. Individual responses/review date/hash were not supplied; do not invent them or withhold the confirmed approval.
- **Blocks:** global gate now satisfied. Publication still needs separate RL-006 permission; implementation and experiments retain their own prerequisites.
- **Effort:** S (half-day review).
- **Risk:** low.
- **Validation:** none; this is a human review gate.

### RL-002 — Scaffold Research Lab working tree (this task)

- **Parent deliverable:** `D-P0`.
- **Owner (proposed):** Member 6 (R-COORD).
- **Dependencies:** `RL-001` (Blocking; satisfied by the owner-confirmed approval recorded in PROJECT_STATUS.md §6).
- **Scope:** create the directory tree described in [`ARCHITECTURE.md`](ARCHITECTURE.md) §2, with per-directory `README.md` placeholders. **Does not** introduce engine code.
- **Deliverables:** `research-lab/PLAN/`, `research-lab/implementation/README.md`, `research-lab/evaluation/README.md`, `research-lab/application/README.md`, `research-lab/reports/README.md`, `research-lab/research/benchmarks/README.md`, planning documents listed in `ARCHITECTURE.md` §2.
- **Acceptance:** directories and READMEs exist; this file (`TASK_GRAPH.md`) exists and resolves all referenced documents.
- **Done when:** merged on main as part of the initialisation PR; verified by inspection.
- **Status:** **In Review** (scaffold exists in the local untracked workspace; RL-001 accepted; no separate scaffold acceptance or PR/merge evidenced).
- **Effort:** S.
- **Risk:** low.

### RL-003 — Add Lab-scoped `.gitignore` and dependency manifest skeleton

- **Owner (proposed):** Member 1 (R-INFRA).
- **Dependencies:** `RL-002`, `RL-013` (Blocking: scaffold and runtime choice).
- **Scope:** add `research-lab/.gitignore` for Python caches, run outputs under `evaluation/runs/`, benchmark data under `research/benchmarks/<name>/data/`, virtualenvs. Add a minimal `research-lab/pyproject.toml` with project metadata, the RL-013-selected runtime, independent uv.lock with resolved transitive pins, and dev dependencies for `pytest`, `ruff`, `pyright`. **Does not** add runtime dependencies yet; those come from P2–P4 tasks when justified.
- **Deliverables:** `research-lab/.gitignore`, `research-lab/pyproject.toml`, independent `research-lab/uv.lock` and runtime/tool pins.
- **Acceptance:** independent frozen-lock environment contains only declared dev dependencies; lint/type tooling runs. Empty pytest collection is expected (normally exit 5, not 0); record actual results. No install during planning.
- **Blocks:** `RL-004`, `RL-005`.
- **Effort:** S.
- **Risk:** low.

### RL-004 — Author `check_independence.py` guard script

- **Owner (proposed):** Member 1 (R-INFRA).
- **Dependencies:** `RL-003` (Blocking).
- **Scope:** implement `research-lab/scripts/check_independence.py` that (a) scans `research-lab/**/*.py` for imports of banned modules listed in [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md) §3; (b) given a diff list, reports any file touched outside `research-lab/`. Exit non-zero on violation.
- **Deliverables:** `research-lab/scripts/check_independence.py`, unit tests under `research-lab/scripts/tests/test_check_independence.py`.
- **Acceptance:** script runs locally and emits `OK` on the current clean tree; adding a dummy `import app.core.config` in a Lab file causes exit code 1 with a clear message.
- **Validation:** inline unit tests cover both positive and negative cases.
- **Effort:** S.
- **Risk:** low.

### RL-005 — Add Lab-scoped CI job

- **Owner (proposed):** Member 1 (R-INFRA).
- **Dependencies:** `RL-003`, `RL-004`, `RL-012` (Blocking: tooling, guard, PG/extension target).
- **Scope:** implement owner-selected D-LAB-CI as dedicated `.github/workflows/research-lab.yaml`, filtered on Lab changes and its own workflow changes. The job runs `ruff`, `pyright`, `pytest`, and `check_independence.py` scoped to `research-lab/`. Also runs `validation/mechanical_checks.py` on the standing documents to keep spec integrity green.
- **Deliverables:** CI workflow change (one small, scoped file edit); passing green run on an initial no-op branch.
- **Acceptance:** CI run on the initialisation PR green; CI run on a PR that only touches `app/` does not fire the lab job (sanity).
- **Decision:** `D-LAB-CI`; selected dedicated-file hosting still requires the scoped boundary exception described in DECISIONS_REGISTER.md. Existing boundary §4 permits only an added job in main-app CI. Do not implement the dedicated workflow before required supervisor approval is evidenced.
- **Effort:** S.
- **Risk:** low.

### RL-006 — Publish GitHub Issues, labels, milestones, Project fields

- **Parent deliverable:** `D-P0`.
- **Owner (proposed):** team review authorises; Member 6 is the publication operator.
- **Dependencies:** `RL-002` through `RL-005` accepted so the workflow is runnable before contributors claim tasks; RL-001 is owner-confirmed Done. Also requires separate explicit owner publication authorization.
- **Scope:** apply the labels and milestones proposed in [`GITHUB_SETUP.md`](GITHUB_SETUP.md); publish the issue drafts from `PLAN/issue_drafts/` as real GitHub Issues; create the Project board with the status columns; wire native issue dependencies where supported.
- **Deliverables:** GitHub Issues `RL-001` through the full backlog; labels; milestones for `P0 ↠ P13`; a Project board.
- **Acceptance:** every issue draft under `PLAN/issue_drafts/` has a corresponding GitHub Issue; cross-references rewritten to issue URLs; `PROJECT_STATUS.md` updated with issue links.
- **Gate:** **DO NOT RUN WITHOUT YOUR EXPLICIT APPROVAL.** This is the single publication step.
- **Effort:** M.
- **Risk:** medium (irreversible once published; a mistake creates visible team noise).

---

## Section 1 — Phase P1 open decisions (series `RL-01x`)

Resolve the Open decisions in [`../Methodology_and_Consistency_Checklist.md`](../Methodology_and_Consistency_Checklist.md) §6, mirrored in [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md). Many P1 tasks can start in parallel because they are supervisor- or pilot-driven, not code-driven.

### RL-010 — Supervisor sign-off package for frozen H1/H3/H4 scope

- **Parent deliverable:** `D-P1`.
- **Owner (proposed role, not assigned):** methodology/scope contributor.
- **Dependencies:** none (independent preparation; supervisor consultation/disposition is an acceptance evidence gate, not a prerequisite to drafting).
- **Decision IDs covered:** PLAN/roadmap D-09, D-17, D-20, D-27; confirmation of existing D-01/D-05 without changing them. D-23/D-24 belong to RL-016, not a duplicated sign-off batch.
- **Scope:** compile a one-page memo per decision citing the proposal passage, record supervisor sign-off (or rejection), update `DECISIONS_REGISTER.md`.
- **Deliverables:** `research-lab/PLAN/supervisor_signoff/<decision_id>.md` for each decision.
- **Acceptance:** every covered decision has a dated supervisor memo attached.
- **Classification:** Ready for local memo preparation; acceptance is Decision-gated on actual supervisor disposition. A rejection/deferment records a completed consultation, not approval to execute affected work.
- **Blocks:** `RL-100` (pilot launch).
- **Effort:** M.

### RL-011 — Design and schedule the pilot

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-010` (Blocking, needs frozen scope). Non-blocking link to `RL-020` and `RL-030` because the pilot design depends on knowing what adapters and baselines exist.
- **Decision IDs covered:** roadmap §E items 10, 11, 12, 13, 14, 18, 20, 21, 22.
- **Scope:** write pilot procedures/criteria for those decisions: δ_pres, δ, u*, share, stratum mix, exclusion feasibility, budget/subset, B4 and judge. No final thresholds are chosen here; final values come from development evidence and signed RL-100 freeze. Pilot runs on **development split only** per roadmap §D.10 and §10.7.
- **Deliverables:** `research-lab/PLAN/pilot_plan.md`; freeze criteria for each value; owners and target dates.
- **Acceptance:** every pilot-dependent decision has a dated row in `DECISIONS_REGISTER.md` showing owner, target date, pass criterion.
- **Blocks:** `RL-100`.
- **Effort:** M.

### RL-012 — Resolve `D-PG-VER` (PostgreSQL version)

- **Owner (proposed role, not assigned):** R-DB contributor.
- **Dependencies:** none.
- **Scope:** select supported stable PostgreSQL and compatible pgvector candidate using ARCHITECTURE.md §7. Historical 16.15 evidence is not the runtime default; verify release/support/build/extension compatibility at implementation.
- **Deliverables:** entry in `DECISIONS_REGISTER.md` with rationale; may require a short reproduction run of `validation/bitemporal_checks.sql` on the chosen version if different from 16.
- **Acceptance:** PG/extension constraints and image tag/digest declared with dated evidence; selected-version reduced-schema log preserved separately. Hand target to RL-005; do not wait for downstream CI to close the decision.
- **Blocks:** `RL-040`, `RL-041`.
- **Effort:** S.

### RL-013 — Resolve `D-ENG-LANG` (engine implementation language)

- **Owner (proposed role, not assigned):** R-ENGINE contributor.
- **Dependencies:** `RL-010` (Non-blocking, for supervisor awareness); runtime evidence gathering and memo preparation may proceed in parallel, but record the awareness handoff before finalizing a decision that needs it.
- **Scope:** select a supported stable compatible language/runtime using ARCHITECTURE.md §7; Python 3.12/3.13 in earlier documents are assumptions, not pins. Document consequences for performance tests (roadmap §9) and for the T1–T5 harness design (SQL-only vs application-level, open decision 17).
- **Deliverables:** D-ENG-LANG memo with dated release/wheel/build/license constraints and runtime requirements for RL-003.
- **Acceptance:** documented runtime/language choice and constraints; hand manifest requirements to RL-003. Do not wait for RL-003's manifest (it depends on this decision).
- **Blocks:** `RL-040` onward (engine code).
- **Effort:** S.

### RL-014 — Resolve A2b implementation and suite D event cardinality

- **Owner (proposed role, not assigned):** methodology/suite contributor.
- **Dependencies:** none.
- **Decision IDs covered:** roadmap §E item 15 (A2b implementation), item 16 (one deletion event per scenario in suite D).
- **Scope:** record decisions with evidence.
- **Deliverables:** D-15/D-16 decision memos and named register updates.
- **Acceptance:** D-15 supervisor disposition, D-16 reasoned cardinality choice; propagate to drafts without changing scenario-level inference or making optional A2b essential.
- **Blocks:** RL-023 task completion; A2b configuration finalization in RL-031 (non-blocking preparation).
- **Effort:** S.

### RL-015 — Ethics determination for optional usability study

- **Parent deliverable:** `D-P1`.
- **Owner (proposed role, not assigned):** R-APP / usability-study scope contributor.
- **Dependencies:** none (supervisor- and ethics-committee driven).
- **Decision IDs covered:** roadmap §E item 19.
- **Scope:** confirm whether the §10.9 optional usability study is attempted, and under which ethics determination.
- **Deliverables:** D-19 memo with actual supervisor/ethics determination or documented skip.
- **Acceptance:** determination evidence and RL-120 study scope updated; no recruitment before approval.
- **Blocks:** `RL-120` optional user-study work only.
- **Effort:** S.

### RL-016 — Supervisor sign-off on script-first synthetic suites and injection suite release

- **Parent deliverable:** `D-P1`.
- **Owner (proposed roles, not assigned):** paired methodology/suite contributor and release-engineering contributor; two contributors are required.
- **Dependencies:** none.
- **Decision IDs covered:** roadmap §E items 23 and 24.
- **Scope:** confirm that own suites `T`, `D`, `G` are the primary evidence for H1/H3/H4; decide on public release of suite G (dual-use review).
- **Deliverables:** D-23/D-24 supervisor memos, register update and release posture propagated to RL-131.
- **Acceptance:** actual signed/dated disposition attached; rejection leaves affected generation/release blocked, not silently authorized.
- **Blocks:** `RL-022`, `RL-023`, `RL-024` (generators), and `RL-131` release posture.
- **Effort:** S.

---

## Section 2 — Phase P2 benchmarks and own suites (series `RL-02x`)

### RL-020 — Pin and mirror LongMemEval

- **Owner (proposed role, not assigned):** R-DB contributor.
- **Dependencies:** `RL-002`, `RL-003` (Blocking: scaffold and ignore rules). RL-016 is not a prerequisite to start or complete this public benchmark mirror.
- **Scope:** pin commit `98d7416` (19 Sep 2025); mirror into `research/benchmarks/longmemeval/` with licence file, SHA-256 hashes, `MANIFEST.txt`. Record oracle counts (30 abstention, 56 single-session-assistant, 470 answerable).
- **Deliverables:** `research/benchmarks/longmemeval/{MANIFEST.txt, LICENSE, README.md}`; data directory under `.gitignore`.
- **Acceptance:** per `PROJECT_ROADMAP.md` §D.2 acceptance; smoke `B0` run reproduces published metric values to within rounding (uses outputs from `RL-030`, so this acceptance may be verified in `RL-025`).
- **Blocks:** `RL-025`, `RL-032`, `RL-102` (H2 experiment).
- **Effort:** M.

### RL-021 — Pin and mirror LoCoMo

- **Parent deliverable:** `D-P2`.
- **Owner (proposed role, not assigned):** R-INFRA/reproducibility contributor.
- **Dependencies:** `RL-002`, `RL-003` (Blocking: scaffold and ignore rules). RL-016 is not a prerequisite to start or complete this public benchmark mirror.
- **Scope:** mirror `locomo10.json`; licence (CC BY-NC 4.0); SHA-256; `MANIFEST.txt`; re-count (10 conversations, 1,986 questions, four questions with empty evidence).
- **Deliverables:** `research/benchmarks/locomo/{MANIFEST.txt, LICENSE, README.md}`.
- **Acceptance:** counts match ledger §C.
- **Blocks:** `RL-025` (descriptive LoCoMo adapter).
- **Effort:** S.

### RL-022 — Own suite G (gate/injection) generator

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-016` (Blocking: release decision), `RL-014` (Non-blocking).
- **Scope:** per roadmap §D.2 activity 3, generate scenarios with injection events, scored questions, canaries, control probes, labels, seed metadata. Deterministic given seed.
- **Deliverables:** `research/benchmarks/own_G/{generator.py, splits/, MANIFEST.txt, README.md}`.
- **Acceptance:** deterministic smoke run produces expected scenario / event / canary counts; splits by scenario-id hash.
- **Blocks:** `RL-103` (H3 experiment).
- **Effort:** M.

### RL-023 — Own suite D (deletion) generator

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-014` (Blocking: event cardinality), `RL-016` (Blocking).
- **Scope:** suite D with canaries including a superseded fact; positive-control (A8) support; never-stated control probes.
- **Deliverables:** `research/benchmarks/own_D/{generator.py, splits/, MANIFEST.txt, README.md}`.
- **Acceptance:** smoke run; reachability of canaries audited (`§4.1 mechanically guaranteed` audit per roadmap §D.2 failure modes).
- **Blocks:** `RL-105` (H4 experiment).
- **Effort:** M.

### RL-024 — Own suite T (temporal) generator

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-014` (Non-blocking), `RL-016` (Blocking).
- **Scope:** suite T with two strata (history-dependent, present-state) and the diagnostic ablations.
- **Deliverables:** `research/benchmarks/own_T/{generator.py, splits/, MANIFEST.txt, README.md}`.
- **Blocks:** `RL-101` (H1 experiment), `RL-100` (pilot run).
- **Effort:** M–L.

### RL-025 — Benchmark adapters (LongMemEval, LoCoMo)

- **Owner (proposed):** Member 4.
- **Dependencies:** `RL-020`, `RL-021`, `RL-030` (Blocking: data and stable scorer interface), matching the full issue draft.
- **Scope:** adapters that do not alter data; implement `(system, question, gold) → score` using official scorers (LongMemEval `evaluation.py`, LoCoMo evaluator).
- **Deliverables:** `research/benchmarks/longmemeval/adapter.py`, `research/benchmarks/locomo/adapter.py`, `tests/`.
- **Acceptance:** adapter runs the official scorer; `B0` smoke matches published trivial-submission metric values.
- **Effort:** M.

### RL-026 — Freeze dev vs test splits for all suites

- **Owner (proposed):** Member 4.
- **Dependencies:** `RL-022`, `RL-023`, `RL-024`, `RL-025` (Blocking).
- **Scope:** per roadmap §D.2 activity 4; splits by scenario-id hash; publish `research/benchmarks/splits/`.
- **Acceptance:** freeze list signed; no scenario appears in both splits.
- **Blocks:** `RL-100` and all `RL-10x` experiments.
- **Effort:** S.

---

## Section 3 — Phase P3 harness, baselines, judge, statistics (series `RL-03x`)

### RL-030 — Evaluation harness skeleton with manifest + stats stub

- **Parent deliverable:** `D-P3`.
- **Owner (proposed):** Member 5 (R-APP, harness owner).
- **Dependencies:** `RL-013` (Blocking: language), `RL-003` (Blocking: pyproject).
- **Scope:** config layer, pinned model, pinned prompt per configuration (§10.4); run manifest; `runs/<ts>/<config>/<suite>/{answers.jsonl, scores.jsonl, per_cluster.jsonl, manifest.json}` layout.
- **Deliverables:** `evaluation/harness/`, smoke `pytest` module.
- **Acceptance:** running a trivial baseline on a dummy suite produces manifests and byte-identical scores on repeat with the same seed.
- **Blocks:** `RL-031`, `RL-032`, `RL-033`, `RL-034`, `RL-025` (adapter interface), `RL-100`.
- **Effort:** M.

### RL-031 — Baselines B0, B1, B2, B2h, B3 (Mem0 pinned)

- **Owner (proposed):** Member 5 (R-APP).
- **Dependencies:** `RL-030` (Blocking); `RL-014` (Non-blocking for A2b).
- **Scope:** B0/B1 raw context, B2 dense raw-turn retrieval, B2h identical P dense/FTS/RRF channels, B3 isolated pinned OSS adapter; controlled/system-specific differences per proposal §10.1.
- **Deliverables:** `evaluation/baselines/{b0,b1,b2,b2h,b3_mem0}/`, pinned commits recorded.
- **Acceptance:** smoke scores on dummy suite; manifests record `model_id`, `temperature`, `seed`, dependency SHA.
- **Blocks:** `RL-100`.
- **Effort:** L.

### RL-032 — B4 Graphiti baseline (conditional)

- **Owner (proposed):** Member 5 (R-APP).
- **Dependencies:** `RL-031`, `RL-011` (Blocking: infrastructure and qualification plan).
- **Scope:** disabled-by-default B4 adapter and bounded dev qualification. Final D-21 inclusion at RL-100 uses end-to-end/frozen-model/cost evidence; preparation does not wait for that downstream decision.
- **Deliverables:** evaluation/baselines/b4_graphiti/ and bounded dev cost/model/capability manifest.
- **Acceptance:** declared adapter smoke and actual dev qualification with frozen models, cost and unsupported capabilities recorded; not automatic final inclusion.
- **Effort:** M.

### RL-033 — LLM-judge harness and perturbation calibration

- **Parent deliverable:** `D-P3`.
- **Owner (proposed):** Member 5 (R-APP, harness track).
- **Dependencies:** `RL-030`, `RL-020`, `RL-011` (Blocking: harness, dev data and validation procedure).
- **Scope:** per roadmap §D.3 activity 3; reference-based grading; perturbation calibration from `[36]`.
- **Acceptance:** runnable judge with human dev agreement/confusion matrix/κ/perturbation evidence under RL-011 procedures. RL-100 freezes final D-22 criterion/sample sizes and validated judge. No unvalidated test scoring; component readiness does not wait for downstream final freeze.
- **Blocks:** `RL-101`, `RL-102`, `RL-103`.
- **Effort:** M.

### RL-034 — Statistics module

- **Parent deliverable:** `D-P3`.
- **Owner (proposed):** Member 1 (R-INFRA, reproducibility-centric). Moved off Member 2 to reduce R-ENGINE blocking density; the stats module has no engine dependency.
- **Dependencies:** `RL-030`.
- **Scope:** cluster bootstrap, sign-flip permutation, Holm correction, Clopper–Pearson at scenario level, non-inferiority at margin — per roadmap §D.3 activity 4 and proposal §10.6.
- **Acceptance:** reproduces `validation/cp_bounds.output.txt` numbers for the C–P arithmetic case; unit tests for each statistic.
- **Blocks:** `RL-110`.
- **Effort:** M.

---

## Section 4 — Phase P4 schema and engine core (series `RL-04x`)

### RL-040 — Full-schema DDL

- **Owner (proposed):** Member 4.
- **Dependencies:** `RL-012` (Blocking: PG version), `RL-013` (Blocking: language), `RL-010` (Blocking: scope frozen).
- **Scope:** author DDL in `implementation/schema/` for `MEMORY`, `MEMORY_VERSION`, `PREDICATE`, `MEMORY_EVIDENCE`, `MEMORY_DERIVATION`, `DELETION_LOG`, `JOB`, audit tables. CHECK constraints for R1–R8; exclusion constraint with `btree_gist` (invariant 4) on single-valued predicates in both ranges; per-user partition keys; `recorded_from` monotonicity trigger.
- **Deliverables:** `implementation/schema/*.sql`.
- **Acceptance:** schema matches proposal §8; reduced-schema SQL assertions still pass against the full schema.
- **Blocks:** `RL-041` through `RL-090`.
- **Effort:** L.

### RL-041 — Migrations

- **Owner (proposed):** Member 4.
- **Dependencies:** `RL-040`.
- **Scope:** up/down migrations under `implementation/migrations/`.
- **Acceptance:** up→down→up restores logical schema/data/constraint equivalence to a fresh up; physical database bytes are not a reproducibility criterion.
- **Blocks:** `RL-050`, `RL-060`, `RL-070`, `RL-080`.
- **Effort:** S–M.

### RL-042 — Transactional supersession, archival, deletion procedures

- **Owner (proposed):** Member 2.
- **Dependencies:** `RL-040`.
- **Scope:** per roadmap §D.4 activity 3; archival procedure enforces invariant 10 (S) ∨ (C).
- **Acceptance:** procedure returns `VALIDATION_ERROR archival_precondition_unmet` on T1/T2 setups.
- **Blocks:** `RL-090` (T1–T5 harness).
- **Effort:** M.

### RL-043 — Retrieval predicates (current/historical/audit-as-of)

- **Owner (proposed):** Member 2.
- **Dependencies:** `RL-040`.
- **Scope:** current/historical point and window/audit predicates; resolve documented proposal/spec shorthand ambiguities through RL-040 review before accepting APIs.
- **Deliverables:** scoped query functions and deterministic worked-example tests.
- **Acceptance:** exact returned-version sets, staging invisibility, Archived ordinary exclusion/audit inclusion and source-correct time checks; no silent predicate change.
- **Blocks:** `RL-060`.
- **Effort:** M.

---

## Section 5 — Phase P5 validation gate, lifecycle (series `RL-05x`)

### RL-050 — Validation gate + assertion-mode classifier

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-041`, `RL-042`.
- **Scope:** assertion-mode classifier (hedge/joke/quote/assistant-guess/injection/user-authored); extractor confidence calibration pre-pilot; gate decisions logged with reason codes per §7.5.
- **Acceptance:** on labelled synthetic candidates, the gate assigns the labelled outcome at ≥ pilot-determined accuracy; property-based tests confirm idempotency.
- **Blocks:** `RL-070`, `RL-103`.
- **Effort:** L.

### RL-051 — Conflict detector + supersession procedure wiring

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-042`, `RL-050`.
- **Scope:** relation classifier and gate-to-transaction supersession wiring.
- **Deliverables:** conflict detector integration with labeled relation/reason and idempotency tests.
- **Acceptance:** a known contradiction triggers supersession once only; uncertain relations hold; transaction rollback preserves integrity.
- **Blocks:** `RL-060`, `RL-101`.
- **Effort:** M.

---

## Section 6 — Phase P6 retrieval, consolidation, lineage (series `RL-06x`)

### RL-060 — Dense + lexical + RRF retrieval

- **Parent deliverable:** `D-P6`.
- **Owner (proposed):** Member 5 (R-APP).
- **Dependencies:** `RL-043`, `RL-051`.
- **Scope:** pgvector exact per user; PostgreSQL FTS (not BM25); RRF k=60; HNSW only in scalability tests.
- **Acceptance:** evidence Recall@k and MRR match harness definitions; snapshot-consistency test (fast-path reads do not observe partial supersession).
- **Blocks:** `RL-070`, `RL-080`, `RL-102`.
- **Effort:** L.

### RL-061 — Minimal consolidation + `MEMORY_DERIVATION` lineage

- **Owner (proposed):** Member 2.
- **Dependencies:** `RL-040`, `RL-042`.
- **Scope:** minimal consolidation (semantic summaries, derived memories) with derivation links; retention variant A9 explicitly as exploratory ablation only.
- **Acceptance:** lineage query returns the expected chain from a derived version back to source versions.
- **Blocks:** `RL-070`, `RL-105`.
- **Effort:** M.

---

## Section 7 — Phase P7 deletion and privacy (series `RL-07x`)

### RL-070 — Deletion closure, §7.7 in-boundary sweep

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-042`, `RL-050`, `RL-060`, `RL-061`.
- **Scope:** transitive derivation closure in a single transaction; remove versions, derived artifacts, lineage rows, queue entries, in-boundary cache entries; boundary-free audit record; disallow re-ingestion within deletion epoch.
- **Acceptance:** T5 passes; every in-boundary channel listed in §7.7 shows no residual retrievable copy after a deletion; never-stated control probe is never answered.
- **Blocks:** `RL-093`, `RL-105`.
- **Effort:** L.

---

## Section 8 — Phase P8 integration, async paths (series `RL-08x`)

### RL-080 — Fast + slow paths; PostgreSQL job queue

- **Parent deliverable:** `D-P8`.
- **Owner (proposed):** Member 5 (R-APP).
- **Dependencies:** `RL-041`, `RL-050`, `RL-060`, `RL-070` (Blocking: deletion/epoch integration; roadmap P8 after P7).
- **Scope:** `FOR UPDATE SKIP LOCKED`, idempotency keys, per-user advisory locks, dead-letter table.
- **Acceptance:** idempotency test — same event re-enqueued N times produces exactly one belief transition.
- **Blocks:** `RL-090`.
- **Effort:** L.

### RL-081 — Replay-clock harness and visibility-lag instrumentation

- **Parent deliverable:** `D-P8`.
- **Owner (proposed):** Member 1 (R-INFRA, reproducibility tooling).
- **Dependencies:** `RL-080`.
- **Scope:** replay clock for the harness; visibility lag measured descriptively (§10 O6).
- **Deliverables:** clock/replay utility, stable-order/time-tie tests and lag instrumentation.
- **Acceptance:** proposal §7.4 replay ordering/reference-time rules tested, no backdated commits, descriptive lag recorded; R3 ties strictly increase per user.
- **Blocks:** `RL-091`.
- **Effort:** M.

---

## Section 9 — Phase P9 testing matrix (series `RL-09x`)

### RL-090 — T1 – T5 archival-precondition harness (invariant 10)

- **Owner (proposed):** Member 4.
- **Dependencies:** `RL-010`, `RL-040`, `RL-041`, `RL-042`, `RL-061`, `RL-070` (Blocking: D-27 and T5 deletion implementation).
- **Status:** **Blocked** on D-27 / roadmap §E item 27 / checklist §6 item 17 (SQL-only vs application-level). RL-010 is the resolution task; no resolution is yet evidenced.
- **Scope:** T1 reject on currently-valid without replacement; T2 reject on historical current belief without replacement (V3-bug regression); T3 accept on superseded via (S); T4 accept on (C) with provenance intact; T5 deletion closure removes derived replacement.
- **Acceptance:** each of T1–T5 passes per §G.
- **Blocks:** release claims on invariant 10 and risk `R10`.
- **Effort:** L.

### RL-091 — Concurrency + replay-clock tests (invariants 3, 4, 9)

- **Owner (proposed):** Member 4.
- **Dependencies:** `RL-040`, `RL-080`, `RL-081`.
- **Scope:** two writers supersede the same row; replay-clock ties (R3); exclusion-constraint under contention (decision 18 pilot).
- **Deliverables:** implementation/tests/concurrency/ and D-18 evidence report.
- **Acceptance:** no invariant violation under contention/retry; strictly increasing recorded instants and atomic close-then-insert; failure investigated, not hidden by dropping constraints.
- **Effort:** M.

### RL-092 — Cross-user isolation + RLS tests (invariants 7, 8 partial)

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-040`, `RL-050`.
- **Scope:** restricted-role RLS, user partitions/service scope and staged-row exposure tests.
- **Deliverables:** isolation/security tests and probe output.
- **Acceptance:** no cross-user context/content exposure with missing/wrong scope; record owner/BYPASSRLS caveats; integration rechecked under RL-094.
- **Effort:** M.

### RL-093 — Deletion closure tests (invariant 8)

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-070`.
- **Scope:** independent all-version/transitive-artifact seven-channel deletion sweep and race probes.
- **Deliverables:** implementation/tests/deletion/ with canary/control outputs.
- **Acceptance:** no residual in-boundary copy and no content/hash in deletion receipt; positive/never-stated controls and exclusions recorded.
- **Effort:** M.

### RL-094 — Full §G matrix green or waived

- **Owner (proposed):** Member 4 (coordinator).
- **Dependencies:** `RL-090`, `RL-091`, `RL-092`, `RL-093`, plus any other test rows added later.
- **Scope:** consolidate every roadmap §G row, including queue and end-to-end integration, not just named test tasks.
- **Deliverables:** signed matrix report with logs/owners and explicit exceptions if permitted.
- **Acceptance:** all mandatory §G rows required by J2/J7 tested; optional exceptions documented. The roadmap's general waiver wording is not permission to waive invariant 10, isolation or deletion evidence; unresolved ambiguity is escalated before phase acceptance.
- **Blocks:** `RL-100`.
- **Effort:** M (coordination; actual implementation is in the per-row tasks).

---

## Section 10 — Phase P10 pilot and controlled experiments (series `RL-10x`)

### RL-100 — Pilot on development split; freeze values

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-010`, `RL-011`, `RL-026`, `RL-031`, `RL-033`, `RL-034`, `RL-094`.
- **Scope:** per roadmap §D.10 activity 1; freeze δ_pres, δ (LUAR), u*, detection share, suite T stratum mix, LongMemEval subset, Graphiti inclusion.
- **Deliverables:** `evaluation/runs/pilot/`, signed freeze list.
- **Blocks:** `RL-101`, `RL-102`, `RL-103`, `RL-105`.
- **Effort:** L.

### RL-101 — H1 experiment (run exactly once)

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-100`, `RL-024`, `RL-051`.
- **Scope:** frozen H1 suite T P−A2 with both co-primary strata, unchanged extraction and non-treatment settings.
- **Acceptance:** one H1 decision requires both §4.1 stratum conditions, with diagnostics and predeclared statistics; not separate hypotheses per stratum.
- **Deliverables:** `evaluation/runs/<ts>/H1/report.md`, `per_cluster.jsonl`.
- **Blocks:** `RL-110`, `RL-130`.
- **Effort:** M.

### RL-102 — H2 experiment

- **Owner (proposed):** Member 4.
- **Dependencies:** `RL-100`, `RL-020`, `RL-025`, `RL-060`.
- **Scope:** frozen H2 LongMemEval_S answerable/evidence treatment; controlled A5/A6 channel removals.
- **Deliverables:** evaluation/runs/<ts>/H2/ report, manifest and per-cluster scores.
- **Acceptance:** decision per §4.1; Recall@k, MRR, nDCG reported.
- **Blocks:** `RL-110`, `RL-130`.
- **Effort:** M.

### RL-103 — H3 experiment

- **Owner (proposed):** Member 3.
- **Dependencies:** `RL-100`, `RL-022`, `RL-050`.
- **Scope:** frozen H3 held-out suite G comparison with identical replayed extractions.
- **Deliverables:** evaluation/runs/<ts>/H3/ report, manifest and per-cluster scores.
- **Acceptance:** decision per §4.1 (ASR AND LUAR).
- **Blocks:** `RL-110`, `RL-130`.
- **Effort:** M.

### RL-105 — H4 experiment

- **Owner (proposed):** Member 2.
- **Dependencies:** `RL-100`, `RL-023`, `RL-070`, `RL-061`.
- **Scope:** frozen H4 scenario-level suite D channel sweep, A8 sensitivity control and re-learning exclusions.
- **Deliverables:** evaluation/runs/<ts>/H4/ report, manifest, per-scenario/channel evidence.
- **Acceptance:** decision per §4.1 (three-conjunct Supported rule); scenario-level C–P bound with u* reference.
- **Blocks:** `RL-110`, `RL-130`.
- **Effort:** M.

---

## Section 11 — Phase P11 statistics and error analysis (series `RL-11x`)

### RL-110 — Statistical reports + error analysis per RQ

- **Parent deliverable:** `D-P11`.
- **Owner (proposed):** co-authored. Section ownership: Member 3 writes H1 + H3; Member 4 writes H2; Member 2 writes H4; Member 1 writes the stats-glue and the error-analysis cross-cut. Member 2 is the integration owner.
- **Dependencies:** `RL-101`, `RL-102`, `RL-103`, `RL-105`, `RL-034`.
- **Scope:** cluster-bootstrap CI, sign-flip permutation, Clopper–Pearson, Holm, intersection-union; qualitative error analysis per stratum.
- **Deliverables:** `reports/H1.md`, `reports/H2.md`, `reports/H3.md`, `reports/H4.md`, `reports/error_analysis.md`.
- **Blocks:** `RL-130`.
- **Effort:** M.

---

## Section 12 — Phase P12 PDT demonstration (series `RL-12x`)

Runs in parallel with evaluation; off the critical path for research success.

### RL-120 — PDT inspector, correction, historical, deletion flows

- **Parent deliverable:** `D-P12`.
- **Owner (proposed):** Member 5 (R-APP). Optional pairing with Member 1 if load warrants.
- **Dependencies:** `RL-042`, `RL-043`, `RL-050`, `RL-060`, `RL-061`, `RL-070`, `RL-080`.
- **Scope:** per roadmap §D.12.
- **Acceptance:** each §11 user flow has an acceptance test; cross-user privacy tests pass.
- **Status note:** can start **in parallel with the P10 experiments** once P8 is stable.
- **Decision linkage:** `RL-015` (ethics) affects any optional user study.
- **Effort:** L.

---

## Section 13 — Phase P13 release and defence (series `RL-13x`)

### RL-130 — Release package

- **Parent deliverable:** `D-P13`.
- **Owner (proposed):** Member 1 (R-INFRA, release engineering).
- **Dependencies:** `RL-101`, `RL-102`, `RL-103`, `RL-105`, `RL-110`, `RL-094`, `RL-120` (if application track is Mandatory).
- **Deliverables:** `release/vX.Y.Z/` with schema, code, suites, configs, docs, licence files.
- **Acceptance:** release is structurally complete with pinned environment, smoke instructions and expected summary; independent execution is accepted under RL-130-REPRO. D-P13 is not complete until reproduction succeeds. This separates package handoff from its downstream validation, not a waiver of reproduction.
- **Effort:** L.

### RL-130-REPRO — Independent reproduction run of the release

- **Parent deliverable:** `D-P13`.
- **Owner (proposed):** Member 4 (R-DB) — intentionally not the release author, so the reproduction is a fair cross-check.
- **Dependencies:** `RL-130`.
- **Deliverables:** `release/vX.Y.Z/REPRODUCTION.md` showing the smoke evaluation re-run on an independent PostgreSQL instance.
- **Scope:** independent clean-environment release smoke execution by non-author.
- **Acceptance:** smoke summary matches `RL-130`'s expected summary to within the declared tolerance; logs/environment attached.
- **Effort:** M.

### RL-131 — Final report + defence deck

- **Parent deliverable:** `D-P13`.
- **Owner (proposed):** Member 6 integrates; Members 2, 3, 4 are co-authors (one hypothesis each); Members 1 + 5 + supervisor are reviewers.
- **Dependencies:** `RL-110`, `RL-130`, `RL-130-REPRO` (Blocking: independent reproduction).
- **Scope:** integrate final report, defense notes, limitations and declared deviations without new research claims.
- **Deliverables:** reports/final_report.md and reports/defence_deck_notes/.
- **Acceptance:** report covers every decision (Supported / Not supported / Inconclusive) with its evidence; independent reviewers per assignments.
- **Effort:** L.

---

## Summary counts

- Phase-level final deliverables: **14** (`D-P0`..`D-P13`).
- Total work packages: **51** (`RL-001`..`RL-131` + `RL-130-REPRO`). Verified by `grep -oE 'RL-[0-9A-Z-]+' TASK_GRAPH.md | sort -u | wc -l`.
- After RL-001: RL-002 is In Review; RL-010/012/013/014/015/016 are Ready for preparation. RL-003 waits for accepted RL-002 + RL-013; RL-020/RL-021 wait for accepted RL-002 + RL-003 and then can run in parallel. RL-016 is a non-blocking release-posture link only, never a start/completion prerequisite for either mirror. Publication and all task-specific gates remain.
- Decision-blocked: RL-090 (D-27); B4 final inclusion remains pilot-dependent, not RL-032 preparation.
- RL-100 is prerequisite-blocked; experiments require its signed freeze plus task-specific prerequisites.
- Critical integration paths branch after RL-040: migrations and transactions converge at gate → conflict → retrieval; transactions also feed lineage. Retrieval + lineage + gate → deletion → async → replay/concurrency; archival/RLS/deletion validation branches converge at RL-094 → RL-100 → experiments → reports → release → reproduction → final report. See DEPENDENCY_MAP.md for exact edges; migrations and transactions are not a sequential pair.
- Parallelisable workstreams:
  - RL-002 scaffold review can proceed alongside P1 decision preparation.
  - RL-010/012/013/014/015/016 decision preparation can proceed independently (with RL-013's non-blocking RL-010 awareness handoff); RL-011 follows accepted RL-010.
  - Benchmark preparation (`RL-020`..`RL-026`) follows its RL-002/RL-003 and decision prerequisites; RL-020/RL-021 can run concurrently after RL-003 acceptance.
  - Harness + baselines + stats (`RL-030`..`RL-034`) may run alongside P1 decision work only after their listed runtime/manifest and task dependencies are accepted.
  - PDT demo (`RL-120`) runs alongside P10/P11.
  - CI + independence tooling (`RL-003`..`RL-005`) runs alongside P1.
- No member is the sole blocker on the critical path. The chain crosses Members 2, 3, 4, and 5.

See [`DEPENDENCY_MAP.md`](DEPENDENCY_MAP.md) for the Mermaid graph.
