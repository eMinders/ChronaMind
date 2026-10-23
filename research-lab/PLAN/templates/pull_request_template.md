# [RL-XXX] <title>

## Linked issue
Closes #<issue-number>

## Summary
<What this PR changes, in a few sentences.>

## Deliverables included
- `<path>` — <what>

## Acceptance criteria (from the issue)
- [ ] Criterion 1
- [ ] Criterion 2

## Local check battery

Paste the actual command output below each check. Any failure must be addressed before marking Ready-for-review.

```
# From research-lab/
$ uv run ruff check .
<output>

$ uv run ruff format --check .
<output>

$ uv run pyright
<output>

$ uv run pytest
<output>

$ uv run python scripts/check_independence.py
<output>
```

If the PR touches any `*.md` standing document:

```
$ cd research-lab && python3 validation/mechanical_checks.py \
    Graduation_Project_Proposal.md \
    Methodology_and_Consistency_Checklist.md \
    CHANGELOG.md
<output>
```

## Independence confirmation (mandatory)
- [ ] I did not modify any file outside `research-lab/` as part of this change.
- [ ] I did not introduce a runtime dependency on the main application (`app/`, `evals/`, `alembic/`, `scripts/` outside the Lab).
- [ ] If I touched `.github/`, it is only to add Lab-scoped files approved under `RL-005` or `RL-006`.

## Dependency confirmation
- [ ] All blocking prerequisite tasks for this issue are `Done`.
- [ ] Dependent tasks notified or linked.

## Reviewer notes
<Anything the reviewer should pay particular attention to.>
