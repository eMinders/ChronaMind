# SCHEDULE.md — Deadline and proposed research-execution schedule

**Timezone:** Africa/Cairo (UTC+3) for all dates and times.
**Initialisation deadline (from the task brief):** Saturday, **24 October 2026, 21:00 Africa/Cairo**.
**Planning closure:** 2026-10-23. RL-001 is Done by owner-confirmed team approval as recorded in PROJECT_STATUS.md §6; the actual team review date/time and reviewed revision are unavailable. The earlier initialisation deadline remains historical planning context, not evidence of implementation.

---

## 1. Initialisation deadline — historical planning target

The original target was **Saturday 24 October 2026, 21:00 Africa/Cairo**. It is a historical planning deadline, not a current commitment or evidence that the package was accepted by that date. The working tree contains the following planning artifacts (not implementation):

- Research Lab planning package under `research-lab/PLAN/`:
  - `ARCHITECTURE.md`
  - `INDEPENDENCE_BOUNDARY.md`
  - `TASK_GRAPH.md`
  - `DEPENDENCY_MAP.md`
  - `ASSIGNMENTS.md`
  - `DECISIONS_REGISTER.md`
  - `PROJECT_STATUS.md`
  - `CONTRIBUTING.md`
  - `GITHUB_SETUP.md`
  - `SCHEDULE.md` (this file)
- Issue templates and PR template under `research-lab/PLAN/templates/`.
- Local-only, unpublished issue drafts under `research-lab/PLAN/issue_drafts/`.
- Scaffolded directories with per-directory `README.md`: `research-lab/implementation/`, `research-lab/evaluation/`, `research-lab/research/benchmarks/`, `research-lab/application/`, `research-lab/reports/`.
- Mechanical checks re-run; result and output quoted in [`PROJECT_STATUS.md`](PROJECT_STATUS.md) §8.

**Not** ready by that deadline, and explicitly deferred to post-approval work:

- GitHub Issues, labels, milestones, Project items (`RL-006`).
- Lab-scoped CI job (`RL-005`).
- `check_independence.py` guard (`RL-004`).
- `pyproject.toml` and `.gitignore` for the Lab (`RL-003`).
- Any engine code (`RL-04x` onwards).

The split is intentional: the initialisation target was for a **reviewable plan**, not implementation; actual acceptance and downstream readiness are tracked in PROJECT_STATUS.md.

---

## 2. Proposed post-approval schedule

The dates below are **indicative legacy estimates**, not commitments or the actual approval date. RL-001 is recorded Done by owner confirmation; the actual approval date/time is unavailable. Rebaseline with real availability and supervisor response times before committing dates. They are grounded in the task graph and the critical path from [`DEPENDENCY_MAP.md`](DEPENDENCY_MAP.md), not in a staffing assumption that has not been confirmed. Replace them with actual dates once each member's availability is known.

| Week | Phase milestone (D-PN) | Targeted work / conditional completion (not current Done state) |
|---|---|---|
| W1 (25–31 Oct 2026) | Scaffold acceptance + decision evidence (conditional targets) | RL-001 already Done; RL-002 separate acceptance/merge; RL-012/013 preparation in parallel; RL-003 waits for RL-002 + RL-013, then RL-004; RL-005 also needs RL-012 and approved CI exception. RL-006 separately authorized only after RL-002–005. |
| W2 (1–7 Nov 2026) | Decisions underway; mirrors only if prerequisites accepted (`D-P1` partial) | `RL-010`, `RL-012`, `RL-013`, `RL-014`, `RL-015`, `RL-016`; RL-020/RL-021 only after RL-002 + RL-003 are accepted; RL-016 is not a mirror prerequisite |
| W3 (8–14 Nov 2026) | Harness skeleton + own-suite generators drafted (`D-P2` partial; closes after RL-026) | `RL-011` pilot procedures, `RL-030`, `RL-022`, `RL-023`, `RL-024` (drafts), then `RL-025` |
| W4 (15–21 Nov 2026) | DDL + migrations + procedures + splits (`D-P4` partial; `D-P2` complete) | `RL-040`, `RL-041`, `RL-042`, `RL-026` |
| W5 (22–28 Nov 2026) | Retrieval + consolidation + gate underway (`D-P4` closes; `D-P5` partial) | `RL-043`, `RL-050`, `RL-061` |
| W6 (29 Nov–5 Dec 2026) | Gate + conflict + retrieval (`D-P5` closes; `D-P6` partial) | `RL-051`, `RL-060`, `RL-031` |
| W7 (6–12 Dec 2026) | Deletion closure + async paths (`D-P6` closes; `D-P7`, `D-P8` close) | `RL-070`, `RL-080`, `RL-081` |
| W8 (13–19 Dec 2026) | Judge + statistics + testing (`D-P3` conditional; `D-P9` partial) | `RL-033`, `RL-034`, `RL-090`, `RL-091`, `RL-092`, `RL-093`; optional RL-032 must qualify before B4 final inclusion, not block core D-P3 when excluded |
| W9 (20–26 Dec 2026) | Testing green or waived (`D-P9` closes); pilot preparation | `RL-094`, pilot plan refined |
| W10 (27 Dec 2026–2 Jan 2027) | Pilot runs; freeze values (`D-P10` partial) | `RL-100` |
| W11–W12 (3–16 Jan 2027) | H1–H4 experiments (`D-P10` closes) | `RL-101`, `RL-102`, `RL-103`, `RL-105` |
| W13 (17–23 Jan 2027) | Statistical analysis + error analysis (`D-P11` closes) | `RL-110` |
| W14 (24–30 Jan 2027) | PDT demo + release packaging (`D-P12` closes; `D-P13` partial) | `RL-120`, `RL-130`, `RL-130-REPRO` |
| W15 (31 Jan–6 Feb 2027) | Final report + defence deck (`D-P13` closes) | `RL-131` |

### Load commentary

- Weeks 4–7 concentrate the engine work (Members 2 and 4). If any P4–P7 task slips, the pilot in W10 moves correspondingly.
- The PDT demo (`RL-120`) is on the parallel application track and does **not** block the critical research path.
- `RL-130-REPRO` runs after `RL-130` lands and is owned by Member 4 so the reproducer is independent of the release author.
- Weeks are 7 days of calendar time, not 7 days of engineering time. Member availability has not been confirmed; the first duty of each owner is to record availability and validate or push back on dates locally in PROJECT_STATUS.md until authorized issue publication.

---

## 3. Deadline hygiene

- No task may be scheduled to complete before its blocking prerequisites can realistically be accepted. Within-week items are ordered by the canonical task prerequisites, not assumed parallel or guaranteed Done. Version verification and unconfirmed availability can move every downstream date.
- The pilot in `RL-100` is the single clearest "go / no-go" moment: experiments do not start until the pilot freeze list is signed.
- The test split is touched **once** per configuration (proposal §10.7). Repeated-run aggregation and result-independent defect fixes follow proposal §§10.6–10.7; do not invent a blanket invalidation rule, select favorable runs, or bypass the declared deviations/freeze process.
- If the W15 deadline for the defence deck is externally fixed, work backwards: `RL-131` needs W15, `RL-130`+`RL-130-REPRO` need W14, `RL-110` needs W13, which means `RL-105` must complete by end of W12. Any slip in `RL-100` (pilot) threatens this chain first.

---

## 4. What to review in this schedule

- Replace calendar weeks with real target dates once availability is known.
- Reassign tasks if any member cannot cover their load (see [`ASSIGNMENTS.md`](ASSIGNMENTS.md) §4 workload commentary).
- Confirm the pilot (W10) window with the supervisor so the sign-off on freeze values does not block experiments.
