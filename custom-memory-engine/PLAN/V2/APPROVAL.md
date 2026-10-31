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

---

## Amendment 1 — r7 blocker resolution (2026-10-31)

The original record above is unchanged. This amendment records a later instruction.

| Field | Value |
|---|---|
| **Authority** | The Project Owner, by explicit written instruction to the maintainer session, authorized the maintainer to "resolve all nine blockers B-01 through B-09", to "make the necessary architectural decisions, using the approved V2 Charter, Architecture, research objectives, security principles, and existing code as the governing constraints", to implement them, to complete #46 and #47, and to publish directly to `main`. The same instruction supersedes the earlier "no roadmap implementation" restriction for that scope |
| **Recorded by** | `elewashy` |
| **Amends (approved normative text)** | `ARCHITECTURE_V2.md` §5 rule 4 (`V2-D35`), §5 rules 2–3 storage (`V2-D36`), §9.3 (`V2-D37`), §12 M7 (`V2-D38`) and M5 (`V2-D43`), §2.2 G2/G1a (`V2-D40`, `V2-D41`); `DECISIONS_V2.md` `V2-D07` enumeration (`V2-D39`); resolves open `V2-D13`; adds gate G-TS (`V2-D42`) |
| **Constraint honoured** | Least scope-expanding option consistent with the Charter in every case. No security, statistical, temporal, provenance, deletion or isolation guarantee was weakened. No gate threshold or acceptance criterion was lowered (G1 and G5 were strengthened only) |
| **Not covered** | Open decisions outside B-01–B-09 (`V2-D14`, `D16`, `D17`, `D30`, `D31`, `D32`, `D34`) remain open. Declaring G0b, or starting W1 beyond the blocker primitives, is not part of this amendment |
| **Record** | `DECISIONS_V2.md` §5; `audits/R7_RESOLUTION_REPORT.md`; `SYNC_LOG.md` §12 |

## Amendment 2 — r9 pre-W1 completion (2026-11-01)

The original record and Amendment 1 are unchanged. This amendment records a later instruction.

| Field | Value |
|---|---|
| **Authority** | The Project Owner, by explicit written instruction to the maintainer session, approved the pending owner decisions (#51 tag `cme-v1.0.0` at `490904d`; #52 criterion 3 superseded; #55 the seven missing API operations; #56 proposals C-P1..C-P6; `MIGRATION_RISK.md` D6-D12; the two `DELETION_ORDER.md` rule changes) and assigned `MA-017`, with authority to make the technical, architectural and research decisions needed to clear every prerequisite of W1 |
| **Recorded by** | `elewashy` |
| **Decisions** | `DECISIONS_V2.md` §6 (`V2-D44` onward) |
| **Boundary (absolute)** | **W1 is not authorized.** No W1 task (`E-010`-`E-020`) is started: no schema migration, no new table, no change to the engine's runtime read/write paths. Permitted, as in Amendment 1: specifications, decision records, pure contract modules (`api.contract`, `model.axes`, `scope.*`) and their tests, CI and tooling. Clearing G0b does not authorize W1; W1 starts only on a separate explicit instruction |
| **Constraint honoured** | No security, statistical, temporal, provenance, deletion or isolation guarantee was weakened; no gate threshold or acceptance criterion was lowered; frozen research records unchanged (`validation/document_hashes.txt` 5/5) |

