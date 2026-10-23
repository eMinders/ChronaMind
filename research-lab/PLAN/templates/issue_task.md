---
name: Research Lab task
about: Create a Research Lab engineering/research task
title: "[RL-XXX] <short title>"
labels: ["rl:kind/task"]
assignees: []
---

<!--
Keep this template structure. Fields in angle brackets must be filled.
Fields marked (optional) may be omitted when not applicable.
-->

## Task ID
RL-XXX

## Phase
<P0..P13>

## Owner (proposed)
<@github-username or `Unassigned`>

## Objective
<One or two sentences describing the goal and the verifiable outcome.>

## Scope
<Bullet list of what is in scope.>

## Out of scope
<Bullet list of exclusions. Important for keeping tasks sized correctly.>

## Deliverables
- `<path>` — <what it contains>
- …

## Acceptance criteria
- [ ] <Objectively verifiable statement 1>
- [ ] <Objectively verifiable statement 2>
- …

## Dependencies
### Blocks
- RL-XXX — <reason>

### Blocked by
- RL-XXX — <reason / prerequisite deliverable>

### Dependency class
<Blocking | Non-blocking | Independent | Review | Decision>

### Can start now?
<Yes | No — waiting for RL-YYY>

## Required tests and evidence
<Specific tests, scripts, or artefacts that must accompany completion.>

## Claim instructions
Comment on this issue with:

```
CLAIMED
Owner: @your-handle
Started: YYYY-MM-DD HH:MM Africa/Cairo
Expected deliverable: <short description>
```

Then self-assign the issue and move the Project card to `In Progress`.

## Progress updates
Comment on this issue when you:
- Make meaningful progress.
- Hit a blocker.
- Submit for review.
- Complete the task (with the output of the acceptance commands pasted inline).

Also update `research-lab/PLAN/PROJECT_STATUS.md` per [`CONTRIBUTING.md`](../CONTRIBUTING.md) §5.

## Effort
<S | M | L>

## Risk
<Low | Medium | High>

## Independence confirmation
- [ ] I will not modify any file outside `research-lab/` for this task.
- [ ] I will not introduce a runtime dependency on the main application.
