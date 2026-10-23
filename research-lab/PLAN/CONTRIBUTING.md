# CONTRIBUTING.md — Research Lab contribution workflow

**Scope:** `research-lab/` only. For main-app contributions see [`../../AGENTS.md`](../../AGENTS.md) at the repository root; those rules apply there, not here.

---

## 1. Independence rules

Before writing any code: read [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md). Every PR that touches `research-lab/` self-reports compliance in the PR template.

Short summary:

- Do not import `app.*`, `evals.*`, `alembic.*`, or any main-app module from Lab code.
- Do not modify any file outside `research-lab/` as part of a Lab PR.
- Research Lab has its own `pyproject.toml`, `.gitignore`, CI job, and (eventually) its own PostgreSQL schema. These are not shared with the main app.

---

## 2. Branching

- Branches off `main` and follow the convention `research-lab/<task-id>-<slug>` (e.g. `research-lab/RL-040-schema-ddl`).
- One task per branch. Multi-task branches are disallowed because they confuse the claim and handoff protocol.
- Rebase on `main` before opening a PR; no force-pushes once the PR is open except to resolve conflicts, in which case comment on the PR before and after.

---

## 3. Claim protocol

Read the issue and check its prerequisites before claiming. After RL-006 publication, a GitHub Issue must be claimed before starting its currently unblocked scope. Use this comment format:

```
CLAIMED
Owner: @github-username
Started: YYYY-MM-DD HH:MM Africa/Cairo
Expected deliverable: <short description matching the issue's Deliverables>
```

Claim procedure:

1. Express interest with the `CLAIMED` comment and self-assign the issue. If another contributor has already expressed interest, coordinate first; do not silently overwrite or duplicate ownership.
2. Confirm the actual contributor and an independent reviewer with the team. The reviewer must not be an author; respect paired-task requirements such as RL-016.
3. Move the issue/project card to `In Progress` if the project board is in use, and update `PROJECT_STATUS.md` §3 with actual owner, timestamp, deliverable and next check-in. Before GitHub publication, record the same local claim in §3 instead.
4. Re-check prerequisites. Start only preparation or implementation that is currently unblocked. An issue being available or claimed does not satisfy supervisor, ethics, technical, task-acceptance, publication or experimental-freeze gates.
5. Work on one task per branch. Submit the task deliverables/evidence in a PR and request the independent review.
6. On acceptance, update the canonical task/status record and dependency map/readiness as required; report the acceptance and downstream handoff on the issue after publication. Do not mark a task Done before its acceptance criteria pass.

**Overlapping interest:** comment to coordinate a pairing or select an alternative available issue before self-assignment. Paired tasks require all specified perspectives; the author(s) cannot review their own work.

---

### Pre-publication bootstrap (RL-001 owner-confirmed team acceptance recorded)

Approval provenance/audit limitations are in PROJECT_STATUS.md §6. Unpublished Ready tasks and RL-002 acceptance work may now be claimed locally in PROJECT_STATUS.md §3 using the same owner/time/deliverable fields and task ID, with accepted prerequisites and reviewer identified. Handoffs and acceptance are recorded locally until RL-006 migrates those records to issues. This applies to foundation/decision work and any technically ready task; it does not authorize GitHub publication, experiments before freeze, or bypass research/ethics gates. RL-006 still needs separate explicit publication authorization and RL-002–005 acceptance. No local claim is recorded by this planning revision.

## 4. Handoff protocol

When task B depends on task A:

1. Task B stays `Blocked` until A is `Done`.
2. The owner of A submits deliverables (code, docs, logs) and the required validation evidence.
3. A enters `In Review`. Reviewers check acceptance criteria in [`TASK_GRAPH.md`](TASK_GRAPH.md).
4. A is marked `Done` **only after** acceptance criteria pass. `PROJECT_STATUS.md` is updated.
5. The owner of A re-evaluates B. If all B prerequisites are satisfied, B's status becomes `Ready`.
6. The owner of A `@`-mentions the proposed owner of B on the B issue with a one-line unblock note.
7. The proposed owner of B claims B per Section 3 before starting.
8. Dependency status and the progress record are updated.

No one auto-assigns or auto-starts a dependent task.

---

## 5. Progress updates

Every meaningful change must be reported on the issue, including:

- What changed, with the file paths touched or the artefact produced.
- Which acceptance checks were run and what the output was.
- Remaining work (sub-tasks or clean-up).
- New blockers discovered.
- Next concrete action.
- Whether downstream work can start.

Mark a task `Done` only after all acceptance criteria pass. Code written but not verified against acceptance criteria is not `Done`.

---

## 6. Pull requests

- One PR per task; title starts with `[RL-XXX]`.
- All PRs are opened as **Draft** until the local check battery (lint, typecheck, unit tests, independence check) is green.
- The PR template at [`templates/pull_request_template.md`](templates/pull_request_template.md) is mandatory.
- Mergeable only after reviewer approval per the pairing in [`ASSIGNMENTS.md`](ASSIGNMENTS.md) §2 "Review pairings".

---

## 7. Local check battery

From `research-lab/` after `uv sync`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv run python scripts/check_independence.py
```

For spec documents (any change under `*.md`):

```bash
python3 validation/mechanical_checks.py \
  Graduation_Project_Proposal.md \
  Methodology_and_Consistency_Checklist.md \
  CHANGELOG.md
```

Each applicable command must exit 0 before marking a PR Ready-for-review. Before RL-003/004 supply tooling and the guard, foundation/decision documentation reviews use existing mechanical/link/preservation checks with unavailable commands explicitly recorded—not fabricated green runs. Before tests exist, pytest's exit 5 means no tests collected, not a passing suite; document it and permit only the empty-scaffold case in RL-005. No blanket suppression of test failures. Documentation-only checks use the existing standard-library scripts without installing the future Lab environment.

---

## 8. Decisions and specification changes

- Decisions follow the resolution protocol in [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md) §3.
- TECHNICAL_SPECIFICATION.md §13 controls changes to Frozen decisions, invariants, rules, predicates, archival/deletion and normative acceptance criteria; use its controlled successor changelog, affected citation audit, mechanical recheck and traceability update. No routine engineering authority bypasses this. Roadmap §E items 1–8 are research Frozen rows; its numbering differs from the spec.
- Changes to the 41-entry reference list require updating the pinned `REFERENCE_LIST_SHA256` in `validation/mechanical_checks.py` with a `CHANGELOG.md` entry.

---

## 9. What NOT to do

- Do not fabricate results, metric values, benchmark scores, or test runs.
- Do not mark a task `Done` because its code was written. Verify its acceptance criteria.
- Do not weaken a failing test by removing assertions or skipping it; investigate and fix the underlying cause instead.
- Do not import the main app or modify files outside `research-lab/`.
- RL-001 approval is not publication or merge permission. Do not create or publish GitHub Issues without separate explicit owner authorization and RL-006 prerequisites. Local bootstrap claim/acceptance records are permitted; normal independent review is still required before merge.
- Do not run experiments against the test split before the pilot freeze list is signed (`RL-100`). The test split is touched once per configuration.
