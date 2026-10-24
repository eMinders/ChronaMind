## Summary
- **Task / Issue:** Closes #`<issue_number>` (`[RL-xxx]`)
- **What changed:**
  - ...

## Verification Checklist (`research-lab/PLAN/CONTRIBUTING.md`)
- [ ] All deliverables in `research-lab/PLAN/TASK_GRAPH.md` for `RL-xxx` are present.
- [ ] No files outside `research-lab/` (except `.github/workflows/research-lab.yaml` for `RL-005`) were modified (`PLAN/INDEPENDENCE_BOUNDARY.md`).
- [ ] Zero imports from root `app.*`, `evals.*`, or `scripts.*`.
- [ ] `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` passes (`ALL HARD CHECKS PASSED`).
- [ ] `diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt` exits `0`.
- [ ] `DECISIONS_REGISTER.md` / `TRACEABILITY_MATRIX.md` updated if applicable.
