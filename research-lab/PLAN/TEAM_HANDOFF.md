# Team handoff — controlled first execution wave

**Status date:** 2026-10-23. The six P1 issues are published and unassigned; see the links in `PROJECT_STATUS.md` §10. This is a concise dispatch guide, not a second task graph. `TASK_GRAPH.md` is canonical for scope/prerequisites; `ASSIGNMENTS.md` for proposed roles/reviews; `PROJECT_STATUS.md` for actual status; `DECISIONS_REGISTER.md` for governance blockers.

## Approval and limits

RL-001 is **Done by owner-confirmed team approval**, recorded in [`PROJECT_STATUS.md`](PROJECT_STATUS.md) §6. This pass does not assert individual member responses, names, signatures, timestamps, issue comments, or a team-reviewed revision/hash; those details were not supplied. The approval does not authorize GitHub publication, merge, implementation beyond accepted task prerequisites, supervisor or ethics decisions, or experiments. Hybrid remains the owner-selected engineering direction, not evidence of implementation or research support. S-07 is not an architecture-choice gate.

## Dispatch wave

“Ready” below means preparation may begin after local claim/reviewer assignment under [`CONTRIBUTING.md`](CONTRIBUTING.md) §3. It does not mean a decision is accepted or that gated implementation may begin. Members are provisional role IDs; record actual names, availability, claim time and review assignment when supplied.

| Task / state | Proposed owner and independent review | Prerequisite / blocker | Deliverable and acceptance boundary | Parallelism |
|---|---|---|---|---|
| **RL-002 — In Review** | M6 / M1 | RL-001 Done; scaffold exists locally | Inspect scaffold, links and required docs; record actual review; merge normally before Done. No PR/merge is evidenced. | Runs alongside P1 decision preparation. |
| **RL-010 — Ready** | Methodology/scope contributor(s); proposed independent reviewer M2 | No task predecessor; supervisor disposition is required evidence for covered decision acceptance | Prepare D-01/D-05 confirmations and D-09/17/20/27 memos; use real dated supervisor decisions or rejection/deferment records. Does not include D-23/24. | Can proceed alongside RL-012/013/014/015/016. Interested contributors coordinate to avoid overload; Member 3 is only a provisional role proposal. |
| **RL-012 — Ready** | R-DB contributor; proposed independent reviewer R-ENGINE | No task predecessor. Compatibility remains unverified. | Supported PostgreSQL + pgvector candidate, dated lifecycle/build/license evidence, image/digest/build evidence and selected-target SQL log if needed. Do not select by version number alone. | Independent of RL-010/013; parallel. |
| **RL-013 — Ready** | R-ENGINE contributor; proposed independent reviewer R-INFRA | RL-010 is a non-blocking awareness link; hand off before finalizing if relevant. Compatibility evidence required. | Runtime/language comparison and dated release/support/wheel/license/build constraints; pass requirements to RL-003. No dependency install, lockfile, or implementation in this planning pass. | Evidence prep may run alongside RL-010; coordinate awareness. |
| **RL-014 — Ready** | Methodology contributor; proposed independent reviewer M2; supervisor for D-15 | No task predecessor; D-15 requires supervisor disposition. | D-15 A2b determination and D-16 reasoned suite-D cardinality choice; preserve scenario-level inference and keep A2b optional if so specified. | Can run with RL-010; contributors coordinate actual workload, no assignment assumed. |
| **RL-015 — Ready** | Ethics-determination contributor; proposed independent scope reviewer; actual supervisor/ethics authority decides | No task predecessor; ethics determination remains open. | Record actual determination or documented skip; no recruitment or participant study before required approval. | Independent parallel work. |
| **RL-016 — Ready** | Paired suite/methodology + release-engineering contributors; proposed independent reviewer M2; supervisor disposition | No task predecessor; actual supervisor and dual-use disposition needed for affected work | Decide D-23/D-24, primary-evidence posture and public/restricted/private release posture for T/D/G; do not publish or generate gated suites before acceptance. | Both contributors must coordinate interest/participation; actual pair is unconfirmed. |
| **RL-003 — Blocked** | M1 / M2 | Requires accepted RL-002 and completed RL-013 runtime decision | Independent dev-only manifest, lock and ignore rules; implementation only after both prerequisites. No installation/lock generation during this closure pass. | Starts after both predecessors accepted. |
| **RL-020 / RL-021 — Blocked** | M4 / M1 respectively; cross-review | Require accepted RL-002 + RL-003 (RL-003 itself awaits RL-013) | Licensed benchmark mirror, manifest hashes and counts. Actual scorer runs remain with RL-025. | The two mirrors may run in parallel after prerequisites; not before. |
| **RL-011 — Blocked** | M3 / M2 + supervisor review | Blocking predecessor RL-010; RL-020/RL-030 are non-blocking context only | Development-only pilot procedures and criteria; no numeric pilot thresholds chosen ahead of evidence. | After RL-010 acceptance; can incorporate benchmark/harness context when available. |

## Claiming and next execution order

Use [`CONTRIBUTING.md`](CONTRIBUTING.md) §3 and the matching task issue draft. Contributors should: (1) read the issue and verify prerequisites; (2) express interest/claim using the agreed GitHub procedure after publication, or a local claim in `PROJECT_STATUS.md` §3 before publication; (3) coordinate overlaps and pairing; (4) confirm the actual contributor(s) and an independent reviewer; (5) start only the work currently unblocked; (6) submit evidence in a task-scoped PR for review; and (7) update canonical status/dependency records only after acceptance. Do not assign all six tasks in advance.

1. Share the six issue links and `ASSIGNMENTS.md` §2; invite team members to state task preferences, current availability, and whether they are willing to pair or review. Member IDs and role shapes are provisional, not assignments.
2. RL-002 remains a separate review: a volunteer reviewer who is not the scaffold author must inspect the files and complete a normal reviewed merge before Done.
3. The six independent preparation tasks may be selected in parallel. RL-016 requires two participating contributors; the team coordinates the actual pair. RL-010/RL-014/RL-016 overlap on proposed Member 3 only, so do not treat those proposals as commitments; invite another qualified methodology contributor or sequence work after actual availability is known, while preserving paired ownership and reviewer independence.
4. On accepted RL-002 and RL-013, dispatch RL-003. Only after RL-003 acceptance may RL-020 and RL-021 begin, in parallel.
5. Keep RL-005 blocked until RL-003, RL-004, RL-012 are accepted **and** the dedicated-file exception under [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md) §5 is actually approved. No `.github/workflows/research-lab.yaml` is created by this handoff.
6. Keep RL-006 blocked until foundation acceptance, real identities/access and separate explicit owner publication authorization. No project board, full backlog, branch, commit, push or remote setting was created by this handoff.

## Governance and technical blockers

| Blocker class | Decision/task; responsible role | Required evidence/action | Downstream impact |
|---|---|---|---|
| Team coordination | RL-002; M6 + M1 | Actual independent scaffold acceptance and normal merge; real member mapping/claims | RL-003 and all tasks depending on accepted scaffold |
| Supervisor decision | D-09/17/20/27 via RL-010; M3, with M4 D-27 technical input and M1 budget input | Dated decision-specific supervisor disposition; do not treat consultation or RL-001 as approval | RL-011, DDL scope, T1–T5 harness design, paid/model calls, optional persona scope |
| Supervisor decision | D-15 via RL-014; M3 | Actual A2b disposition | A2b finalization and related diagnostics; no silent change to frozen methodology |
| Supervisor / release safety | D-23/24 via RL-016; M3+M1 | Actual acceptance of own suites as primary evidence and documented dual-use release posture for G | Suite generation/public release and final release posture |
| Ethics | D-19 via RL-015; M5 routes to authorized supervisor/ethics body | Actual determination or documented decision to skip; approval before recruitment | Optional RL-120 participant study only |
| Version/compatibility | D-25/RL-012, D-ENG-LANG/RL-013, then RL-003; M4/M2/M1 | Current supported-release evidence, compatibility/build/license tests, digest and reproducible lock evidence | RL-003/005/030/040/041; no guessed pins |
| Supervisor exception | D-LAB-CI / RL-005; M1 drafts, M6 routes | Boundary §5 documented approval for dedicated workflow file; boundary §4 currently allows only an added job | RL-005 dedicated workflow; then RL-006. Not authorized by RL-001. |
| Policy/technical boundary | Prohibited library-import conflict; M2 architecture, M1 dependency review, M3 routes | Reconcile boundary against proposal/project requirements through applicable formal approval before prohibited imports | Affected API/ORM/tracing implementations |
| Task-specific technical acceptance | Proposal/spec ambiguities, impact analysis §4; M2/M4 | Map semantics against authoritative passages; use spec §13 for actual contract conflict | Schema/API and later full-engine acceptance |
| Pilot / experimental freeze | D-10–14/18/21/22, RL-100; M3 with M4/M5 | RL-011 procedures, development evidence and all listed pilot prerequisites; signed freeze before test scoring | H1–H4 held-out runs, analysis and release |

## Not in this wave

Engine implementation, benchmark mirrors before prerequisites, T1–T5 execution, CI workflow creation, GitHub publication, user recruitment, pilot, and H1–H4 experiments are not authorized merely by RL-001. Keep each status distinct: planned, ready for preparation, blocked, in review, accepted, implemented, tested, and experimentally supported are not interchangeable.
