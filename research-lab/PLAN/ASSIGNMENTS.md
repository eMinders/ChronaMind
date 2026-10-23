# ASSIGNMENTS.md — Six-person task distribution (PROVISIONAL)

**Status:** planning shape accepted under owner-confirmed RL-001; **actual identities, availability, task preferences and commitments remain provisional**. Current dispatch/readiness is PROJECT_STATUS.md §10. No real identities or individual RL-001 response records were supplied. Assignment proposals below are not actual issue assignments; contributors self-select work through the claiming procedure. Members 1–6 are **neutral placeholders**; each is an equal team member. Nobody is privileged as the user, permanent lead, or default decision owner. Real GitHub usernames must be supplied before any issue is published under `RL-006`.

**Information needed before publication (`RL-006`):**

- Full name and GitHub username for each of Members 1–6.
- Confirmation each has write access.
- Self-declared primary skill area per member (database / Python backend / evaluation and statistics / frontend-for-PDT / DevOps / research methodology).
- Each member's weekly availability for the research track.
- Timezone (relevant to the Africa/Cairo deadlines in [`SCHEDULE.md`](SCHEDULE.md)).

Until these are provided, this document is a **shape** of a plan, not a commitment. Any member may be swapped between role shapes on skill match.

---

## 1. Role shapes

The role shapes describe clusters of work, not people. When identities are known, the team assigns the closest skill match. All members are equal; a role shape does not confer authority outside its listed task cluster.

| Role shape | Primary responsibilities | Task clusters |
|---|---|---|
| **R-COORD** — Phase coordinator (rotates per phase) | Keeps the current phase's `PROJECT_STATUS.md` entry current, chases review bottlenecks, prepares the phase handoff memo. **Not** a decision-maker above other members; **not** permanently assigned to one person. See §2 for the rotation. | Per-phase coordination plus the shared release work in `RL-130`/`RL-131`. |
| **R-ENGINE** — Memory-engine architect | Transactional procedures, retrieval predicates, consolidation + lineage, H4 experiment, part of statistical report authorship. | `RL-013`, `RL-042`, `RL-043`, `RL-061`, `RL-105`, part of `RL-110` |
| **R-METHOD** — Evaluation methodologist and gate owner | Pilot design, own-suite generators (T, D, G), validation gate, conflict detector, H1 + H3 experiments, cross-user tests, deletion closure. | `RL-011`, `RL-014`, `RL-022`, `RL-023`, `RL-024`, `RL-050`, `RL-051`, `RL-070`, `RL-092`, `RL-093`, `RL-100`, `RL-101`, `RL-103`, part of `RL-110` |
| **R-DB** — Database / schema specialist | PG-version decision, full-schema DDL authorship, migrations, archival (T1–T5) harness, concurrency tests, H2 experiment, benchmark adapters, split freeze. | `RL-012`, `RL-020`, `RL-025`, `RL-026`, `RL-040`, `RL-041`, `RL-090`, `RL-091`, `RL-094`, `RL-102`, `RL-130-REPRO`, part of `RL-110` |
| **R-INFRA** — Infrastructure, tooling, reproducibility, statistics | Lab `.gitignore`+`pyproject`, independence guard, Lab CI job, LoCoMo mirror, statistics module, release package authorship, judge perturbation calibration support, pilot-plan review. | `RL-003`, `RL-004`, `RL-005`, `RL-021`, `RL-034`, `RL-081`, `RL-130` |
| **R-APP** — Baselines, async paths, PDT demonstration | Harness skeleton, baselines B0..B4, async paths and job queue, PDT demonstration, judge harness. | `RL-030`, `RL-031`, `RL-032`, `RL-033`, `RL-060`, `RL-080`, `RL-120` |

Governance tasks (`RL-001` approval, `RL-010`/`RL-015`/`RL-016` supervisor-sign-off, `RL-006` publication) are **not** attached permanently to any single person. §2 names the proposed owner for each based on content area and distributes them across members.

---

## 2. Provisional six-member assignment

These are provisional role/coordination proposals, not confirmed assignments or commitments. Contributors should self-select from eligible issues, state availability/preferences, and coordinate review capacity before a claim is accepted. Governance duties are not assigned by this table alone.

| Person | Role shape | Governance / coordination | Rationale |
|---|---|---|---|
| **Member 1** | R-INFRA (proposed) | Proposed phase P13 coordination and release package `RL-130`; proposed paired contributor for `RL-016` with a methodology/suite contributor. | Provisional role shape only; participation in RL-016 must be confirmed by the two contributors at claim time. |
| **Member 2** | R-ENGINE | Phase P4 coordinator. | Owns the largest technical workstream; coordinates P4 integration review. |
| **Member 3** | R-METHOD (proposed) | Proposed phase P2 coordination and methodology/scope decision contribution. | Provisional role shape only; RL-010/RL-014/RL-016 task claims are not reserved to Member 3. Coordinate actual workload and allow other qualified contributors to self-select. |
| **Member 4** | R-DB | Phase P9 coordinator; owner of `RL-094` (§G matrix sign-off). | Testing matrix is heavily database/invariant-centric. |
| **Member 5** | R-APP (proposed) | Possible phase P12 coordination and optional-study ethics task fit; no assignment is confirmed. | Role-fit suggestion only; contributors self-select and ethics decisions remain with authorized supervisor/university bodies. |
| **Member 6** | R-COORD / R-APP support | Phase P0, P1, P6 coordinator; **operator** of `RL-006` publication after separate explicit owner authorization; `RL-131` final-report author (co-authored). | Spreads coordination load off every other member in the earliest and middle phases; final report is co-authored by Members 2, 3, 4, 6. |

**Coordinator rotation:**

| Phase | Coordinator |
|---|---|
| P0 initialisation | Member 6 |
| P1 decisions | Member 6 |
| P2 benchmarks + own suites | Member 3 |
| P3 harness + baselines | Member 5 |
| P4 schema + engine core | Member 2 |
| P5 gate + lifecycle | Member 3 |
| P6 retrieval + consolidation + lineage | Member 6 |
| P7 deletion closure | Member 3 |
| P8 async paths | Member 5 |
| P9 testing matrix | Member 4 |
| P10 pilot + experiments | Member 3 |
| P11 statistics + error analysis | Member 2 |
| P12 PDT demonstration | Member 5 |
| P13 release + report | Member 1 |

The coordinator for a phase **does not** decide for the team: the role is to keep the status board accurate, chase reviewers, and prepare the phase handoff memo.

### Review pairings (no self-approval on significant deliverables)

| Significant deliverable | Author | Reviewer (proposed) |
|---|---|---|
| RL-002 scaffold acceptance | Member 6 | Member 1 |
| RL-003/004/005 tooling/guard/CI | Member 1 | Member 2 (CI exception requires supervisor) |
| RL-010 scope/budget/harness memos | Contributor(s) selected at claim time; D-27 technical evidence can be prepared with a database-skilled contributor | Independent reviewer not among authors; actual supervisor disposition remains required |
| RL-012 PG/extension target | Member 4 | Member 2 |
| RL-013 runtime target | Member 2 | Member 1 |
| RL-014 A2b/cardinality | Methodology contributor selected at claim time | Independent reviewer not the author; supervisor disposition required for D-15 |
| RL-015 ethics memo | Member 5 | Member 3 scope review; actual ethics/supervisor determination |
| RL-016 own-suite/release memos | **Two contributors required:** one suite/methodology perspective and one independent release-engineering perspective; proposed roles may be M3 + M1 but are not assigned | Reviewer distinct from both authors; actual supervisor disposition and release authorization remain required |
| RL-020/021 mirrors | Member 4 / Member 1 | Member 1 / Member 4 cross-review |
| RL-006 publication | Member 6 operator | Member 1 checks listing; owner authorizes publication |
| `RL-040` Full-schema DDL | Member 4 | Member 2 |
| `RL-042` Txn procedures | Member 2 | Member 4 |
| `RL-050` Validation gate | Member 3 | Member 2 |
| `RL-060` Retrieval | Member 5 | Member 2 |
| `RL-061` Consolidation + lineage | Member 2 | Member 3 |
| `RL-070` Deletion closure | Member 3 | Member 2 |
| `RL-080` Async paths | Member 5 | Member 4 |
| `RL-090` T1–T5 harness | Member 4 | Member 3 |
| `RL-100` Pilot freeze | Member 3 | Member 2 + supervisor |
| `RL-101`..`RL-105` Experiments | per owner | one peer + supervisor sign-off on freeze |
| `RL-110` Statistical reports | each hypothesis owner co-writes their RQ section | Members 2 + 3 cross-review |
| `RL-130` Release package | Member 1 | Member 4 (reproduces from scratch) |
| `RL-131` Final report | Members 2, 3, 4, 6 co-authors | Members 1 + 5 + supervisor |

---

## 3. Per-member task portfolios

Each portfolio lists owned tasks, review responsibilities, prerequisite rules and estimates. RL-001 is Done by owner-confirmed team approval; current Ready/In Review/Blocked state is only in PROJECT_STATUS.md §10. Individual response and reviewed-revision evidence was not supplied. Subsequent readiness is recalculated from TASK_GRAPH.md, not automatically from these portfolios. First-wave reviewer pairings are in §2; they supplement the later review burden summaries.

### Member 1 — R-INFRA + release package

- **Proposed task-fit examples (not assignments):** `RL-003`, `RL-004`, `RL-005`, `RL-021`, `RL-034`, `RL-081`, `RL-130`; potential release-engineering perspective in paired `RL-016`.
- **Review burden:** `RL-120` privacy acceptance and general PR independence checks; no self-review of RL-130. Member 4 independently reproduces the release.
- **Ready now:** paired RL-016 decision preparation; RL-021 waits for RL-002 + RL-003. RL-003 waits for RL-002 + RL-013; RL-005 also requires RL-004 + RL-012 and the dedicated-workflow exception.
- **Must wait for:** `RL-030` for `RL-034` (stats module; needs harness interface); `RL-080` for `RL-081` (replay clock).
- **Effort estimate:** M (infra + mirrors + stats) + L (release package authorship at the end).
- **Load note:** infrastructure and release authorship; no self-approval of release reproduction. Task graph ownership governs over earlier portfolio prose.

### Member 2 — R-ENGINE

- **Proposed task-fit examples (not assignments):** runtime/evidence work (`RL-013`), `RL-042`, `RL-043`, `RL-061`, `RL-105`, and co-author role on `RL-110` / `RL-131` if selected.
- **Review burden:** `RL-040` DDL, `RL-050` gate, `RL-060` retrieval, `RL-070` deletion, `RL-100` pilot freeze; Member 3 reviews `RL-090` per §2.
- **Ready now:** `RL-013` runtime decision preparation; RL-010 awareness is non-blocking but should be handed off before finalizing if relevant.
- **Must wait for:** `RL-040` for `RL-042`/`RL-043`, then `RL-040` + `RL-042` for `RL-061`; `RL-100` for `RL-105`.
- **Effort estimate:** L — engine core, derivation lineage, H4, H4 section of the report.
- **Load note:** no longer carries the stats module (now Member 1) or the full `RL-110` write-up (now shared); this frees ~1 week of serial work.

### Member 3 — R-METHOD

- **Provisional task-fit examples (not assignments):** methodology scope decisions (`RL-010`), suite-related work (`RL-014`, one paired RL-016 perspective), `RL-011`, `RL-022`, `RL-023`, `RL-024`, `RL-050`, `RL-051`, `RL-070`, `RL-092`, `RL-093`, `RL-100`, `RL-101`, `RL-103`, and report contributions if selected. The first-wave overlap is a workload risk, not an assignment.
- **Review burden:** `RL-090` T1–T5 harness.
- **Load note:** RL-010, RL-014 and one side of paired RL-016 overlap this proposed skill profile. Do not assume Member 3 owns all three; contributors should self-select, coordinate actual availability, and preserve the second RL-016 perspective and independent reviewer.
- **Must wait for:** `RL-010` for `RL-011`; `RL-016` for `RL-022`, `RL-024`; `RL-014`+`RL-016` for `RL-023`; `RL-041`+`RL-042` for `RL-050`.
- **Effort estimate:** heavy — the longest sequence of blocking tasks (`RL-050 → RL-051 → RL-070 → RL-093`). Delegate `RL-022` or `RL-024` to Member 4 if availability drops.

### Member 4 — R-DB

- **Proposed task-fit examples (not assignments):** database/compatibility work (`RL-012`, `RL-020`, `RL-025`, `RL-026`, `RL-040`, `RL-041`, `RL-090`, `RL-091`, `RL-094`), independent reproduction (`RL-130-REPRO`), and report contributions if selected.
- **Review burden:** `RL-042` txn procedures, `RL-080` async paths, `RL-130` release reproducibility input.
- **Ready now:** RL-012 target-evidence preparation; RL-020 waits for RL-002 + RL-003.
- **Must wait for:** RL-010/012/013 for RL-040; RL-010/040/041/042/061/070 for RL-090; RL-130 for independently authored RL-130-REPRO.
- **Effort estimate:** heavy database track; well parallelised with Member 2's engine track.

### Member 5 — R-APP (backend focus) + ethics memo

- **Proposed task-fit examples (not assignments):** ethics determination (`RL-015`), harness/baseline work (`RL-030`–`RL-033`), retrieval/async/PDT tasks (`RL-060`, `RL-080`, `RL-120`).
- **Review burden:** none on the critical path; may review harness-adjacent PRs.
- **Ready now:** RL-015 ethics-determination work; no recruitment. RL-030 waits for RL-013 + RL-003.
- **Must wait for:** `RL-030` for `RL-031`; `RL-031`+`RL-011` for `RL-032`; `RL-043`+`RL-051` for `RL-060`; `RL-041`+`RL-050`+`RL-060`+`RL-070` for `RL-080`; the full P4–P8 engine stack for `RL-120`.
- **Effort estimate:** heavy — baselines + harness + retrieval + async + PDT demo. If overloaded, hand `RL-120` to a joint Member 5 + Member 1 team.

### Member 6 — Early-phase coordinator + publication operator + report author

- **Tasks owned:** `RL-002` (scaffold tree — already drafted in this package), **operator of `RL-006`** publication (owned by the team as a whole; Member 6 executes under the approved plan), `RL-131` final report (co-author).
- **Coordination duties:** phases P0, P1, and P6.
- **Review burden:** independence check-ins; mid-flight dependency bookkeeping; PR status consolidation.
- **In Review now:** `RL-002` separate scaffold acceptance; verify the scaffold and arrange normal review/merge.
- **Must wait for:** every `RL-10x` and `RL-11x` for `RL-131`.
- **Effort estimate:** S (foundation + coordination through P1) + S (coordination through P6) + L (final report at the end).
- **Load note:** Member 6 is not the engineering critical path. If the team wants a more engineering-heavy slot for Member 6, swap with Member 5 on `RL-120` or with Member 1 on `RL-034`.

---

## 4. Workload balance commentary

Rough effort tally (S = small, M = medium, L = large):

| Person | S | M | L | Comment |
|---|---|---|---|---|
| Member 1 | 3 | 3 | 1 | Infra + stats + release authorship. Independent reproduction belongs to Member 4. |
| Member 2 | 1 | 4 | 2 | Engine core + H4; stats and report-writing shared. |
| Member 3 | 2 | 6 | 3 | Longest critical-path chain; still the methodology owner. |
| Member 4 | 2 | 5 | 3 | DB and benchmark-adapter heavy; parallel to Member 2. |
| Member 5 | 1 | 4 | 3 | Harness + baselines + retrieval + async + PDT demo. |
| Member 6 | 3 | 2 | 1 | Coordination through P6 + final report author. |

No single person owns every blocking task on the critical path: it crosses Members 2, 3, 4, and 5. The deepest single-owner stretch is Member 3's `RL-050 → RL-051 → RL-070 → RL-093`, which is methodologically coherent and acceptable for one owner but is explicitly flagged as a load signal.

Governance / supervisor work is distributed:
- Methodology sign-off (`RL-010`) → a methodology contributor chosen at claim time; Member 3 is a provisional fit, not a commitment.
- Ethics for optional user study (`RL-015`) → Member 5 (user study owner).
- Own-suites release memo (`RL-016`) → paired methodology/suite and release-engineering contributors; Member 3 + Member 1 are provisional role-fit examples only.
- Pilot sign-off (`RL-100`) → Member 3 + supervisor; peer review by Member 2.
- Experiment review → one peer + supervisor sign-off on freeze (not any single reviewer).
- Final report (`RL-131`) → Members 2, 3, 4, 6 co-authored; Members 1 + 5 + supervisor reviewers.

---

## 5. Phases where six-way parallelism is not feasible

Not every phase can productively absorb six active contributors. The matrix below records the realistic active-member count per phase and where the remaining members work in parallel.

| Phase | Members productively active in-phase | Where others work |
|---|---|---|
| P0 | 2 (Member 6 scaffold acceptance; Member 1 independent review) | RL-001 Done; independent P1 decision preparation can proceed |
| P1 | Up to six preparation tasks available for self-selection; actual contributor count and pairings unconfirmed | Coordinator may facilitate preference/availability discussion; no ownership is presumed |
| P2 | 5 (M1 LoCoMo mirror; M3 three generators; M4 LongMemEval + adapters + splits; M6 coordinates) | M2 begins engine design (`RL-013` done; prepares `RL-040` review notes); M5 begins `RL-030` harness skeleton once `RL-013` + `RL-003` are accepted |
| P3 | 4 (M5 harness + baselines + judge; M1 stats; M4 adapters; M6 coordinates on independence) | M2 reviews harness; M3 drafts gate design notes |
| P4 | 3 (M4 DDL + migrations; M2 procedures + retrieval predicates + consolidation lineage; M5 continues harness/baselines independently of DDL) | M1 continues stats + prepares reproduction scripts; M3 drafts gate implementation; M6 coordinates handoff memo |
| P5 | 1–2 (M3 gate + conflict detector; M2 reviews) | M4 tests + T1–T5 harness scaffolding; M5 continues baselines; M1 stats; M6 coordinates |
| P6 | 2 (M5 retrieval; M2 consolidation + lineage) | M3 begins deletion-closure design; M4 concurrency tests; M1 prepares release scripts; M6 coordinates |
| P7 | 1–2 (M3 deletion closure; M2 reviews) | Others continue P4–P8 work in parallel |
| P8 | 2 (M5 async paths; M1 replay clock) | M4 concurrency tests; M3 cross-user tests; M2 reviews |
| P9 | 5 (M4 coord + T1–T5; M3 cross-user + deletion-closure tests; M4 concurrency; M5 end-to-end smoke; M2 reviews) | M1 release preparation; M6 coordinates handoff memo |
| P10 | 3 experiment owners across 4 experiments (M2 H4; M3 H1 + H3; M4 H2), after M3's accepted pilot | M1 writes `RL-110` stats glue; M5 PDT; M6 coordinates |
| P11 | 4 (co-authored reports) | M5 PDT; M1 prepares release package (independent reproduction follows RL-130) |
| P12 | 1–2 (M5 PDT; M1 or M3 supports if ethics triggers a user study) | Everyone else writes P13 release artefacts |
| P13 | 5 (M1 release package; M4 subsequent independent reproduction; M6 final report; M2/3 co-authors) | M5 ships PDT demo video |

A phase with fewer than six active owners is explicitly documented so no member is forced into an artificial task to "stay busy". Members with no active in-phase assignment pick up:

- Reviews of PRs already in the queue.
- Pending documentation (handoff memos, `PROJECT_STATUS.md` updates).
- Preparation of their next-phase deliverable.
- Backlog items from [`TASK_GRAPH.md`](TASK_GRAPH.md) whose prerequisites are satisfied.

---

## 6. Remaining coordination details (not a reopened RL-001 review)

1. Confirm identities and GitHub usernames (replace Member 1..6 placeholders).
2. Confirm the role-shape mapping; any member may prefer a different cluster.
3. Allocate review time under the approved pairings; mutual reviewers remain independent of each other's authored deliverable. Record any actual reassignments, do not invent commitments.
4. Decide whether to split `RL-120` (PDT demo) from Member 5 to a Member 5 + Member 1 pair if availability is tight.
5. Confirm the coordinator rotation; the rotation is a proposal and may be re-negotiated once availability is known.
6. Confirm the supervisor is single-sourced or multiple (relevant for `RL-010`/`RL-015`/`RL-016` scheduling).
