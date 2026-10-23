# PROJECT_STATUS.md — Authoritative Research Lab progress board

**Status:** hand-updated. Must match the actual repository state and the GitHub Issues once `RL-006` publishes them.
**Last updated:** 2026-10-23, first-wave issue publication. RL-001 Done by owner-confirmed team approval; per-member response evidence is unavailable. Six P1 preparation issues are published; no implementation or experiment completion added.
**Owner of this document:** the phase coordinator for the current phase (rotation defined in [`ASSIGNMENTS.md`](ASSIGNMENTS.md) §2).

---

## 1. Overall status

- **Current phase:** D-P0 **In Review**, pending separate RL-002 scaffold acceptance/merge; RL-001 is **Done** by owner confirmation. Earlier specification-finalization evidence remains historical.
- **Parallel next work:** P1 decision preparation is **Ready**; six first-wave GitHub issues are published with no assignees. No contributor has claimed or completed those tasks. First-wave handoff: §10.
- **Research experiments:** none run. H1–H4 remain `NOT STARTED` per [`../TRACEABILITY_MATRIX.md`](../TRACEABILITY_MATRIX.md) §1.
- **Independence boundary:** declared in [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md); not yet enforced in CI (gated on `RL-005`).
- **Engineering direction:** Hybrid owner-selected for implementation planning; [F-01/S-07 disposition](../review/FEASIBILITY_REVIEW_DECISIONS.md). Advisory comparison evidence remains separate from the V4 contract; no new Frozen row or supervisor/team signature.
- **Workspace evidence:** `research-lab/` is untracked; scaffold exists locally, but no initialization PR or merge is evidenced. Lab manifest, lockfile, independence guard and CI do not exist. The six first-wave issues were published to the configured GitHub repository; no project board or full backlog was published. The historical finalization script is absent.

---

## 2. Phase-level status

For each phase: status, the final deliverable's acceptance posture, the current blocker, the active members, and the phase coordinator (from [`ASSIGNMENTS.md`](ASSIGNMENTS.md) §2 rotation).

| Phase | Final deliverable | Status | Acceptance posture | Active members (proposed) | Coordinator | Current blocker |
|---|---|---|---|---|---|---|
| P0 | `D-P0` initialisation package | **In Review** | RL-001 Done; scaffold exists; RL-002 acceptance/merge pending | M6 scaffold; M1 independent review | M6 | Separate RL-002 acceptance/merge |
| P1 | `D-P1` decision log + pilot plan | Not Started | Six decision-preparation issues are published and unassigned; no contributor claims recorded | Work is self-selected; proposed role fit and independent review pairings remain provisional | Coordinator role provisional | Decision evidence and supervisor/ethics outcomes; RL-001 satisfied |
| P2 | `D-P2` benchmark + own-suite freeze bundle | Not Started | — | M1 (RL-021), M3 (RL-022/023/024), M4 (RL-020, RL-025, RL-026) | M3 | `D-P1` decisions D-16, D-20, D-23, D-24 |
| P3 | `D-P3` harness + baselines + judge + stats | Not Started | — | M1 (RL-034), M5 (RL-030/031/033) | M5 | `D-P1` (D-ENG-LANG, D-20) |
| P4 | `D-P4` schema + procedures + retrieval predicates | Not Started | — | M2 (RL-042/043/061), M4 (RL-040/041) | M2 | RL-010/012/013; pilot margins do not block DDL |
| P5 | `D-P5` validation gate + conflict detector | Not Started | — | M3 (RL-050/051) | M3 | `D-P4` |
| P6 | `D-P6` retrieval + consolidation + lineage | Not Started | — | M2 (RL-061), M5 (RL-060) | M6 | `D-P4`, `D-P5` |
| P7 | `D-P7` deletion closure | Not Started | — | M3 (RL-070) | M3 | `D-P4`, `D-P5`, `D-P6` |
| P8 | `D-P8` async paths + queue | Not Started | — | M1 (RL-081), M5 (RL-080) | M5 | `D-P4`–`D-P7` (deletion/epoch integration) |
| P9 | `D-P9` §G matrix green | Not Started | — | M2 (reviewer), M3 (RL-092/093), M4 (RL-090/091/094), M5 (end-to-end smoke) | M4 | D-27 for RL-090; `D-P4`–`D-P8` for the rest |
| P10 | `D-P10` pilot freeze + four experiments | Not Started | — | M2 (H4), M3 (pilot + H1 + H3), M4 (H2) | M3 | `D-P2`, `D-P3`, `D-P9`, pilot |
| P11 | `D-P11` statistical reports + error analysis | Not Started | — | M1 + M2 + M3 + M4 co-authored | M2 | `D-P10` |
| P12 | `D-P12` PDT demonstration | Not Started | — | M5 (RL-120); optional M1 pairing | M5 | `D-P4`–`D-P8` |
| P13 | `D-P13` release + final report + defence | Not Started | — | M1 (RL-130), M4 (RL-130-REPRO), M6 (RL-131 lead), M2+M3+M4 co-authors | M1 | `D-P9`, `D-P10`, `D-P11` |

---

## 3. Work-in-progress

No task claims are recorded. The six first-wave GitHub issues are open and unassigned; issue availability is not evidence of an accepted claim or contributor commitment.

Column layout for entries, once populated:

| Task | Owner | Started (Africa/Cairo) | Expected deliverable | Next check-in |

---

## 4. Blocked tasks and reasons to unblock

| Task | Blocked on | What unlocks it |
|---|---|---|
| RL-004/005/006 | Foundation chain; RL-005 boundary exception; RL-006 publication authorization | RL-003 → RL-004; RL-012 + RL-003/004 and approved CI exception → RL-005; RL-002–005 + explicit publication permission → RL-006. |
| RL-003 → RL-020/RL-021 | RL-002 + RL-013, then RL-003 | Accept scaffold/runtime, create independent manifest/lock/ignore rules, then parallel mirrors. |
| `RL-090` (T1–T5 harness) | D-27 and components | Supervisor harness-design decision; RL-010/040/041/042/061/**070** accepted. |
| B4 final inclusion (not RL-032 preparation) | D-21 | RL-032 development qualification under RL-011 plan; RL-100 freezes inclusion based on declared cost/model criteria. |
| `RL-100` pilot | RL-010/011/026/031/033/034/094 | Scope/plan, frozen splits, baselines/judge/stats and full §G sign-off accepted. |
| RL-101/102/103/105 | RL-100 + task-specific prerequisites | Development pilot complete and experimental freeze signed before test scoring. |

---

## 5. Pending reviews

| PR / review item | Author | Reviewer(s) | Note |
|---|---|---|---|
| RL-002 scaffold acceptance | Member 6 (proposed coordinator) | Member 1 (independent reviewer) | Existing local scaffold; inspect files/checks and arrange normal reviewed merge. No PR, merge or separate scaffold acceptance is evidenced. |

---

## 6. Decisions required

See [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md). Highest-priority open decisions and their proposed owners:

- **D-09, D-17, D-20, D-23, D-24, D-27** — require supervisor involvement. `D-09`/`D-17`/`D-20`/`D-27` are grouped under `RL-010` (Member 3). `D-23`/`D-24` are grouped under `RL-016` (Members 3 + 1). `D-19` ethics is under `RL-015` (Member 5). Suggested: batch supervisor sessions by decision owner, not by any single team member.
- **D-25 (D-PG-VER), D-ENG-LANG:** pending verified target/runtime selection; no completion date promised. Component/progress boundaries are owner-authorized engineering selections, not implemented work. D-LAB-CI selects dedicated-file hosting, but the existing boundary only permits an added job; its §5 supervisor-approved exception is pending.
- **S-07:** superseded architecture-choice question; see owner record. Not an extra architecture gate. Academic acceptance is not evidenced; existing research/supervisor decisions remain open.

### RL-001 approval record — Done

**Authority/evidence:** the owner's message “Final Planning Closure and Team Execution Readiness” explicitly confirms completion of the team's RL-001 review/approval. Recorded on **2026-10-23** (workspace date); actual team review/meeting date and time are not supplied. This closes RL-001 without fabricating six individual responses.

**Reviewed revision/hash:** not supplied and cannot be established from Git (`research-lab/` is untracked). The received planning snapshot was fingerprinted before this pass as SHA-256 `b4c41b9a89759be0fde5e7cea823381224c5a346bb27ed1ebe81133f81b7c091` (sorted compact JSON mapping PLAN files plus Lab README/roadmap/traceability to their SHA-256s). This identifies the inspected workspace only, **not a claimed team-reviewed revision**. Later edits are listed in CHANGELOG.md.

**Unavailable audit details:** individual identities/responses, signatures, reviewed hashes, meeting timestamps and issue comments. Obtaining them later does not reopen the confirmed approval. No separate RL-002 merge, supervisor/ethics exception, RL-006 publication permission or experimental freeze is inferred.

---

## 7. Upcoming handoffs

Handoff rows below describe the first few phase handoffs. Full list is maintained inside [`TASK_GRAPH.md`](TASK_GRAPH.md) §A.

| From | To | Deliverable | Timing |
|---|---|---|---|
| Member 3 → supervisor / team | RL-010 decision memos and recorded disposition evidence | P1 partial; no date commitment |
| Member 2 / Member 4 → Member 1 / Member 2 review | RL-013 runtime and RL-012 PostgreSQL evidence for downstream RL-003/RL-005/RL-040 | Independent preparation; no date commitment |
| Member 3 + Member 1 → supervisor / Member 2 review | RL-016 paired D-23/D-24 memos | P1 partial; no date commitment |
| Member 5 → supervisor / ethics authority | RL-015 determination memo; no recruitment | Before any optional user study; no date commitment |
| Member 4 → Member 2 | Accepted DDL ready for procedure authorship (`RL-040`) | After RL-010/012/013 and review; no date commitment |
| Member 1 → Member 4 / Member 2 | Accepted release package ready for independent reproduction (`RL-130`) | Only after the release prerequisites; no date commitment |

These are dependency handoffs, not claims or commitments; calendar estimates remain in [`SCHEDULE.md`](SCHEDULE.md).

---

## 8. Mechanical check evidence

Verification rerun on 2026-10-23: the mechanical command below exited 0 with all hard checks passed; existing REVIEW soft hits remain. In this continuation, local Markdown targets/anchors, task graph acyclicity, full dependency-map synchronization, CP arithmetic reproduction, and edited-Markdown whitespace were also checked; fresh outcomes are in CHANGELOG.md. Historical saved validation outputs were not overwritten. These checks do not validate the engine.

```
$ cd research-lab && python3 validation/mechanical_checks.py \
    Graduation_Project_Proposal.md \
    Methodology_and_Consistency_Checklist.md \
    CHANGELOG.md
...
SUMMARY: ALL HARD CHECKS PASSED
```

The `[REVIEW] result-like phrasing hits in proposal: Counter({'outperform': 1, 'results show': 1, 'outperforms': 1})` line reported after the final `PASS` is a `REVIEW`-labelled soft hit, not a hard failure. The occurrences are in the proposal's §4.1 wording ("The proposal does not predict that P will outperform any external system") and similar hedged phrases that are part of the authoritative text, flagged for ongoing audit per `validation/mechanical_checks.py` rather than being new result claims.

---

## 9. Update protocol

Each contributor updates this document when they:

1. Claim a task — move the task into §3 "Work-in-progress" with their name and the claim timestamp.
2. Submit a meaningful deliverable — add the artefact path.
3. Hit a blocker — move the task to §4 "Blocked tasks" and name what unlocks it.
4. Submit work for review — move to §5 "Pending reviews".
5. Complete a review — move the task to the next phase in §2 or mark it `Done`.
6. Complete or hand off a task — add a row to §7 "Upcoming handoffs" and notify the next owner on the GitHub Issue.

A task is **not** marked `Done` solely because its code was written. Its acceptance criteria and required validation must be satisfied (see each task's acceptance criteria in [`TASK_GRAPH.md`](TASK_GRAPH.md)).

## 10. First-wave distribution handoff

Canonical **current readiness** view; task contracts remain in TASK_GRAPH.md, people/review pairings in ASSIGNMENTS.md, blockers in DECISIONS_REGISTER.md §4. RL-001 is owner-confirmed Done. Six first-wave issues are published and remain unassigned; the project board/full backlog are not published. No task claim or implementation is recorded here. `Ready` means eligible for the specified preparation; it does not mean that supervisor/ethics outcomes or compatibility have been accepted.

| State / task | Proposed owner / role | Prerequisites now | Deliverable / acceptance |
|---|---|---|---|
| Done: RL-001 | Team; M6 records | Owner confirmation §6 | Approval recorded; individual responses, signatures, reviewed revision/hash and actual review date/time unavailable |
| In Review: RL-002 | M6 R-COORD; M1 reviews | RL-001 satisfied; scaffold exists | Verify scaffold/links/mechanical checks; normal reviewed merge before Done |
| Ready: [RL-010](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/1) | Methodology/scope contributor(s); proposed reviewer M2; D-27 technical evidence and budget support may be paired | RL-001 Done; no blocking task prerequisite | Prepare D-01/05 confirmations and D-09/17/20/27 records; actual supervisor dispositions required for affected decisions. D-23/24 are separate RL-016 decisions. Member 3 is a provisional role-fit only; claims are self-selected. |
| Ready: [RL-012](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/2) | R-DB contributor; proposed R-ENGINE independent reviewer | RL-001 Done; no blocking task prerequisite | Dated supported-version and pgvector evidence, image/build/license compatibility and target-specific log if target differs; selection remains open until verified. |
| Ready: [RL-013](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/3) | R-ENGINE contributor; proposed R-INFRA independent reviewer | RL-001 Done; RL-010 awareness is non-blocking | Runtime comparison and compatibility evidence → RL-003; prep may run in parallel, but record awareness handoff before finalizing if it affects selection. |
| Ready: [RL-014](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/4) | Methodology/suite contributor; proposed independent reviewer M2; supervisor for D-15 | RL-001 Done; no blocking task prerequisite | Prepare D-15 A2b supervisor disposition and D-16 cardinality rationale; preserve scenario-level inference. No contributor is assigned. |
| Ready: [RL-015](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/5) | Contributor selected at claim time; proposed independent scope review; supervisor/ethics authority decides | RL-001 Done; no blocking task prerequisite | Actual ethics/supervisor determination or documented skip; no recruitment or study before required approval. |
| Ready: [RL-016](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/6) | Two contributors required: suite/methodology + release-engineering perspectives; proposed independent reviewer M2; supervisor disposition | RL-001 Done; no blocking task prerequisite | Prepare D-23/24 and public/restricted/private posture for T/D/G; generators and publication remain gated by actual disposition. No pair is assigned. |
| Blocked: RL-003 | M1 R-INFRA; M2 reviews | RL-002 separate acceptance and RL-013 runtime choice not Done | Independent dev-only manifest/lock/ignore rules and later tool validation; do not install or create lockfile before prerequisites |
| Blocked: RL-020 / RL-021 | R-DB / R-INFRA contributors; cross-review | Both RL-002 and RL-003 must be accepted; RL-003 itself awaits RL-013. RL-016 is non-blocking only for RL-020 and is not a prerequisite for RL-021. | Pinned mirror manifests/licenses/recounts; mirrors then run in parallel; scorer runs belong to RL-025 |
| Blocked: RL-011 | M3 R-METHOD; M2 + supervisor review | RL-010 decision package not accepted; RL-020/RL-030 are non-blocking awareness | Pilot procedure and criteria only; no numeric threshold chosen before evidence; approval/preparation status is not a pilot run |
| Blocked: remaining tasks | Per ASSIGNMENTS.md | Listed task predecessors, decision dispositions, acceptance or pilot gates remain unmet | RL-004–006, RL-022–026, RL-030–034, RL-040–043, RL-050–051, RL-060–061, RL-070, RL-080–081, RL-090–094, RL-100–103, RL-105, RL-110, RL-120, RL-130, RL-130-REPRO, RL-131; re-evaluate each task independently as predecessors are accepted |

**First-wave issues:** [RL-010 #1](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/1), [RL-012 #2](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/2), [RL-013 #3](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/3), [RL-014 #4](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/4), [RL-015 #5](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/5), [RL-016 #6](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/6). All remain open and unassigned.

**Dispatch order:** share these issue links and `ASSIGNMENTS.md` §2 with the team so contributors can browse and self-select. Ask interested contributors to state preferences, pairing interest and actual availability, then use each issue's claim procedure; no issue is assigned yet. A volunteer independent reviewer handles RL-002. RL-010/012/013/014/015 may be selected in parallel; RL-016 needs two contributors with distinct methodology/suite and release-engineering perspectives plus an independent reviewer. Member 3's proposed fit overlaps RL-010/014/016 but is not a commitment; redistribute eligible work or sequence it after availability is known without weakening paired ownership or reviewer independence. Batch supervisor sessions without duplicate decision ownership. Accept prerequisites individually, then dispatch RL-003; afterward mirrors, RL-004 and harness become eligible per their edges. Dedicated RL-005 requires its supervisor-approved boundary exception; RL-006 additionally needs publication permission and confirmed identities/access.

Use CONTRIBUTING.md §3 local bootstrap claims with real identity, actual time, reviewer and accepted prerequisites. The untracked scaffold still needs normal reviewed merge; no branch or merge is authorized by this status entry. Owner actions: confirm real member/role mapping and availability, and give separate explicit publication permission later if desired. Academic/ethics outcomes and experimental freeze cannot be replaced by owner confirmation.
