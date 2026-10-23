# DECISIONS_REGISTER.md — Open decisions for the Research Lab

**Status:** mirrors [`../Methodology_and_Consistency_Checklist.md`](../Methodology_and_Consistency_Checklist.md) §6 and [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §E for the research decisions, and adds Lab-local architecture decisions. Updates to the methodology checklist remain authoritative for research decisions; this register is a working view with owner proposals. RL-001 is Done by owner-confirmed team approval (PROJECT_STATUS.md §6); this does not approve any open supervisor, ethics, technical or pilot decision.

**Legend.** State ∈ {Frozen, Open, Pilot, Supervisor}; decision conditions qualify the task edges in TASK_GRAPH.md. Owner-selected Hybrid/S-07: [F-01](../review/FEASIBILITY_REVIEW_DECISIONS.md). Hybrid is not a new Frozen row; RL-001 is independently Done by owner-confirmed team approval in PROJECT_STATUS.md §6. Routine engineering decisions follow ARCHITECTURE.md §7 and the owner's engineering authority; research/ethics changes retain existing change control.

**Numbering caution:** PLAN D-xx follows roadmap §E; TECHNICAL_SPECIFICATION.md uses a different Dec# table (e.g. spec 12 = RRF, PLAN D-12 = u*). Preserve all existing Frozen rows; identify each decision by name/source, never infer state from a matching number.

---

## 1. Research decisions carried from the roadmap

(Items 1–8 are **Frozen in V4** and are listed here for completeness; they must not be silently changed.)

| ID | Decision | State | Proposed owner | Blocks |
|---|---|---|---|---|
| D-01 | H1 primary contrast is **P − A2** | Frozen | — | — |
| D-02 | H1 endpoint form: history-dependent (sup.) + present-state (NI at δ_pres) | Frozen | — | — |
| D-03 | Persona layer optional, off in P for H1–H4 | Frozen | — | — |
| D-04 | H4 inference level: scenario-level C–P primary | Frozen | — | — |
| D-05 | H4 "Supported" rule: zero in-boundary leaks ∧ bound ≤ u* ∧ positive control ≥ share | Frozen | — | — |
| D-06 | Archival precondition: (S) ∨ (C); branch (A) removed | Frozen | — | — |
| D-07 | 41-entry reference list byte-identical to V3 | Frozen | — | — |
| D-08 | Minimal consolidation essential; retention and persona summaries not essential | Frozen | — | — |

| ID | Decision | State | Proposed owner | Target gate | Blocks |
|---|---|---|---|---|---|
| D-09 | Supervisor confirmation of H1 scope (incremental vs combined) | Open — Supervisor | Member 3 (methodology author) | `RL-010` | `RL-011`, `RL-100` |
| D-10 | δ_pres (H1 present-state NI margin) | Open — Pilot | Member 3 | `RL-100` | `RL-101`, `RL-110` |
| D-11 | δ (LUAR non-inferiority margin for H3) | Open — Pilot | Member 3 | `RL-100` | `RL-103`, `RL-110` |
| D-12 | u* (H4 informativeness limit) | Open — Pilot | Member 3 | `RL-100` | `RL-105`, `RL-110` |
| D-13 | Positive-control detection share (H4) | Open — Pilot | Member 3 | `RL-100` | `RL-105`, `RL-110` |
| D-14 | Suite T stratum mix | Open — Pilot | Member 3 | `RL-100` | `RL-101`, `RL-110` |
| D-15 | Implement A2b configuration | Open — Supervisor | Member 3 | `RL-014` | Final optional A2b configuration/diagnostics; RL-014 also resolves D-16 before RL-023. Not a separate hard block on baseline preparation. |
| D-16 | One deletion event per scenario in suite D | Open | Member 3 | `RL-014` | `RL-023`, `RL-105` |
| D-17 | Persona layer implementation (keep as A10 only vs enable) | Open — Supervisor | Member 3 (methodology) + Member 5 (PDT impact) | `RL-010` | Optional persona scope in RL-120; core demonstration remains allowed after engine prerequisites, persona off in confirmatory P. |
| D-18 | Exclusion constraint feasibility on full schema under concurrent writers | Open — Pilot | Member 4 | `RL-091` evidence | Full-engine invariant-4 validation and P9 acceptance, not initial `RL-040` DDL needed to test it. Keep mandatory exclusion constraint pending evidence; fallback conflict follows spec §13. |
| D-19 | Ethics determination for optional usability study | Open — Supervisor + Ethics | Member 5 (PDT demo owner) | `RL-015` | `RL-120` (user study) |
| D-20 | LLM budget / hosted vs local model / LongMemEval subset | Open — Supervisor | Member 3 (scope impact) + Member 1 (cost / reproducibility) | `RL-010` | Actual model calls and final subset/freeze; RL-030 dummy skeleton not blocked by budget. |
| D-21 | Include Graphiti (B4) | Open — Pilot | Member 3 | `RL-100` | Final B4 inclusion in frozen experiments; RL-032 adapter/dev qualification proceeds under RL-011 plan without presuming inclusion. |
| D-22 | Judge agreement criterion; scenarios per suite | Open — Pilot | Member 3 | `RL-011` procedure → `RL-033` development validation → `RL-100` final freeze | Test scoring/final experimental freeze; no circular dependency requiring RL-100 completion before constructing its judge. No threshold chosen in this pass. |
| D-23 | Script-first synthetic suites as primary evidence for H1/H3/H4 | Open — Supervisor | Member 3 (suite author) + Member 1 (release-engineering perspective) | `RL-016` | `RL-022`, `RL-023`, `RL-024` |
| D-24 | Release of injection suite G | Open — Supervisor | Member 3 + Member 1 (same `RL-016` memo) | `RL-016` | `RL-131` |
| D-25 | PostgreSQL version (14 / 16 / 17 / 18) | Open | Member 4 | `RL-012` | `RL-040`, `RL-041`, `RL-005` |
| D-26 | Unverified source items (parts of [20]–[29] not read; [5], [16], [30]–[37] beyond abstracts) | Open — Supervisor | Member 3 (methodology correctness) | — | — (claim-level only) |
| D-27 | When and how to execute T1–T5 (SQL-only vs application-level) | Open — Supervisor + Pilot | Member 4 | `RL-010` | `RL-090` |

---

## 2. Lab-local architecture decisions (new)

| ID | Decision | State | Proposed owner | Target gate | Blocks |
|---|---|---|---|---|---|
| D-ENG-LANG | Engine language/runtime; prefer supported compatible stable Python, patch pin after verification | Open | Member 2 | `RL-013` | `RL-003`, `RL-030`, `RL-040`, engine tasks; decision hands requirements to manifest, does not wait for it. |
| D-LAB-CI | Dedicated path-filtered `research-lab.yaml` workflow | Owner-selected engineering target; boundary exception pending; implementation Not Started | Member 1 (R-INFRA) | `RL-005` | RL-003/004/012; boundary §4 permits only an added job, so §5 supervisor approval of dedicated-file exception must precede implementation. RL-001 is Done and does not satisfy this exception. |
| D-ENGINE-SUBMOD | Module contracts in ARCHITECTURE.md §8; tests under `implementation/tests/` | Owner-authorized engineering selection; review incremental APIs in P4 | Member 2 | `RL-040` design review | Component interface handoffs per TASK_GRAPH.md; no separate architecture-choice freeze. |
| D-GITHUB-DEPS | Use GitHub native issue dependencies vs issue-reference text | Proposal | Member 6 (publication operator) | `RL-006` | `RL-006` |
| D-PROGRESS-TRACKER | PROJECT_STATUS.md canonical progress; future GitHub board mirrors issue state | Owner-authorized engineering selection; no board published | Phase coordinator rotation | `RL-001` team workflow review | No second review-level status snapshot; RL-006 remains publication-gated. |

---

### Engineering rationale (2026-10-23)

Dedicated Lab CI is preferred over editing main-app CI to isolate dependency/runtime and path-filter behavior; trade-off is one small workflow, verified by RL-005. This selection does not override INDEPENDENCE_BOUNDARY.md §5: submit the narrow dedicated-file exception through its existing research-decision/supervisor process before implementation. No exception or supervisor response is evidenced. Stable component boundaries avoid a second engine/test tree; names may refine through normal component review without changing semantics. A single status board avoids drift with review snapshots; future GitHub state must be mirrored, not treated as an unrecorded approval. Version pins are **not** selected yet: no Lab manifest/lock exists and upstream metadata checks in the closure pass are recorded in ARCHITECTURE.md §7; install/build and runtime compatibility remain unverified. ARCHITECTURE.md §7 specifies evidence required for RL-012/013/003 and fallback choice. No research decision here is declared approved by engineering judgment.

## 3. Resolution protocol

Each decision follows this flow:

1. Owner drafts a 1-page memo under `research-lab/PLAN/decisions/<id>.md` with: current value, evidence, alternatives, reason for selection, consequences, supervisor sign-off if required.
2. For Pilot items, the memo is attached to the pilot plan `research-lab/PLAN/pilot_plan.md` and the row's `State` becomes `Pilot — in progress` with an owner and target date.
3. On resolution, the row is updated here, in `TECHNICAL_SPECIFICATION.md §2`, and in `PROJECT_ROADMAP.md §E` (keep the three views consistent — the mechanical checks guard against stale wording).
4. Until separately authorized RL-006 publication, record decision acceptance locally with provenance and notify the team; afterward announce it on the tracking issue. Generate decision bodies from this register and the decision template, not nonexistent individual decision drafts.

A decision becoming Frozen never silently rewrites any Frozen row above; a new row is added and the previous Open row is marked `Superseded by D-xx` with a date.

## 4. Blocker routing and required evidence

Canonical blocker classification; current task readiness is PROJECT_STATUS.md §10. No item below is approved by RL-001 alone.

| Class / item | Responsible role | Required evidence / action | Downstream impact |
|---|---|---|---|
| Owner decision: Hybrid/S-07 | Owner | Already selected; F-01 evidence retained, no further architecture vote | No unresolved architecture-choice gate |
| Team coordination: RL-002, role mapping | M6 coordinator; M1 scaffold reviewer; owner confirms real mapping | Existing scaffold inspection, actual reviewer acceptance and normal merge; real claim identities/access/availability | RL-003 and mirrors; publication identities still missing |
| Academic supervisor: D-09/17/20/27 via RL-010 | M3; M4 owns D-27 technical evidence; M1 budget support | Named scope/budget/harness memos and actual supervisor dispositions; rejection uses controlled change/deferment | RL-011/040/090, paid calls, final pilot/freeze; optional persona only |
| Academic supervisor / owner engineering: D-15/16 via RL-014 | M3; M2 review | A2b supervisor disposition; reasoned cardinality choice preserving scenario inference | RL-023; optional A2b config/diagnostic finalization |
| Academic supervisor: D-23/24 via RL-016 | M3 + M1 paired; M2 review | Synthetic-evidence acceptance and injection release/dual-use policy | RL-022/023/024 and release posture RL-131 |
| Ethics determination: D-19 via RL-015 | M5; supervisor/ethics authority | Actual determination/approval or documented skip before recruitment | RL-120 optional study only |
| Version/compatibility: D-25, D-ENG-LANG via RL-012/013; RL-003 | M4 PG; M2 runtime; M1 lock | Dated release/support/license/changelog/wheel evidence, actual target build/digest, selected-schema test, independent locked resolution | RL-003/005/030/040/041; metadata observations do not close decisions |
| Academic supervisor exception: D-LAB-CI / boundary §5 | M1 drafts; M6 routes | Dedicated-file exception must be recorded through boundary §5's checklist/supervisor process; only an added CI job currently permitted. RL-001 approval does not satisfy or waive this exception. | RL-005 dedicated workflow, then RL-006; no exception implemented here |
| Academic supervisor / safety exception: prohibited library imports | M2 architecture; M1 dependency review; M3 routes | Reconcile FastAPI/SQLModel/Langfuse/metrics boundary conflict with proposal/project rules; required scoped exception before prohibited imports | Applicable API/ORM/tracing implementation; permitted standalone DTO/SQL planning may proceed |
| Task-specific acceptance: proposal/spec ambiguities in existing impact analysis §4 | M4 + M2 RL-040 design review; supervisor if semantic change | Passage/entity/rule/predicate/lineage mapping; controlled spec §13 change if true conflict. Also report general P9 waiver wording versus mandatory J2/J7 tests; do not waive mandatory evidence | Schema/API acceptance, full-engine and RL-094 sign-off |
| Technical prerequisite: RL task DAG | Task owners; independent reviewers in ASSIGNMENTS.md | Accepted predecessor artifacts and tests; no Ready state from expected completion dates | All implementation/integration tasks follow TASK_GRAPH.md, not broad phase guesses |
| Publication/claim authorization: RL-006 | Owner explicit publication permission; M6 operator | RL-002–005 accepted, real identities/access, native-support check and local-only templates; local bootstrap claims allowed now | Remote issues/board only; no remote action or template wrappers authorized |
| Pilot evidence: D-10–14/18/21/22 | M3 pilot; M4 concurrency; M5 judge/B4 | RL-011 procedures, dev-only evidence, RL-091 concurrency and RL-032/033 qualification; numeric values not chosen now | RL-100 final freeze; B4 remains conditional, judge qualification precedes freeze |
| Experimental freeze: RL-100 → H1–H4 | M3; M2 + supervisor review | RL-010/011/026/031/033/034/094 accepted and signed versions/models/prompts/splits/statistics/values freeze | RL-101/102/103/105 held-out scoring, reports and release |
| Claim-level evidence: D-26 | M3 methodology | Read exact source passages before adding claims; update ledger/audit if changed | New affected claims only, not blanket implementation block |

No other owner approval is needed for routine in-contract engineering. Request real identities/availability for dispatch and separate publication permission when eligible; supervisor, ethics and pilot gates are resolved by their responsible authorities/evidence.
