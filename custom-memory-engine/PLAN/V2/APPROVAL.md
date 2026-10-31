# V2 Approval Record

| Field | Value |
|---|---|
| **Decision** | ChronaMind V2 (`PLAN/V2/`) is **approved as the project's canonical implementation plan** |
| **Status** | **Approved / Canonical Implementation Plan** |
| **Approved by** | The Project Owner, by explicit written instruction to the maintainer session: *"The latest audit is accepted. I formally approve V2 as the canonical implementation plan."* |
| **Approval date** | 2026-10-31 |
| **Recorded by** | `elewashy` (maintainer account through which the tracker and repository are operated) |
| **Basis** | The r4.1 audit (`TRANSITION_AUDIT.md` §7–§8, `TRACEABILITY_REPORT.md`), which the Project Owner explicitly accepted |
| **Plan revision approved** | r4.1 content of this directory (r2 architecture, r3 statistics, r4 backlog, r4.1 reporting corrections) |
| **Supersedes (for post-v1.0.0 work)** | The *planning* content of `PROJECT_ROADMAP.md`, `PLAN/TASK_GRAPH.md`, `PLAN/SCHEDULE.md`, `PLAN/ARCHITECTURE.md`. `PLAN/DECISIONS_REGISTER.md` is superseded where `DECISIONS_V2.md` conflicts with it |
| **Does not supersede** | `Graduation_Project_Proposal.md`, `Methodology_and_Consistency_Checklist.md`, `TECHNICAL_SPECIFICATION.md` §3.3–§3.5, any `evidence/*` record, or `CHANGELOG.md`. The frozen research record (`validation/document_hashes.txt`) is unchanged |

## What approval does and does not authorise

**Authorised by this approval:**

- `PLAN/V2/` is the canonical plan. Where V2 documents conflict with the v1 planning
  documents listed above for post-v1.0.0 work, V2 wins.
- Records marked `ADOPTED` in `DECISIONS_V2.md` and `Normative` in `STATISTICS.md` and
  `USER_MODEL.md` are now **binding**.
- Milestones W0–W8 and issues #50–#80 are the canonical backlog.

**Not authorised by this approval:**

- **Starting roadmap implementation.** The Project Owner stated that roadmap
  implementation (W0, W0b, W1 and later waves) will be authorised in a **separate
  instruction**. Until then:
  - no roadmap task is executed;
  - no implementation PR is opened for any wave;
  - no engine code is changed for roadmap work.
- **Changing the approved architecture or acceptance criteria.** Any such change needs a new
  `V2-Dnn` record and Project Owner approval.

## Readiness at approval (unchanged by approval)

- **W0:** ready to start once implementation is authorised.
- **W0b:** ready after G0.
- **W1:** not ready. It requires:
  - `MA-011`'s cross-tenant test to fail first;
  - `MA-016`'s M3 specification;
  - #47's restricted `app_role` before G1.

## State of `main` at approval

- `main` = `394dcf2` (merge of PR #49, `RL-070` deletion security, merged 2026-10-31 after
  the three fixes in `d087eb7`).
- Full CI-scope suite: 1423 collected, **1402 passed, 21 skipped**.
- The V2 baseline is measured by `E-002` when W0 is authorised. These figures are not
  a substitute for that measurement.

## History

| Date | Status | Record |
|---|---|---|
| 2026-10-31 | Proposed (r1–r4.1) | `README.md` revisions, `SYNC_LOG.md` §1–§8 |
| 2026-10-31 | **Approved / Canonical Implementation Plan** | this file |
