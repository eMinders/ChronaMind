# Contributor Workflow (`research-lab/`)

**Scope:** Common workflow, branching, code style, and pull-request rules for every task (`RL-001`–`RL-132`) in `research-lab/`. Individual issue drafts reference this file instead of repeating boilerplate instructions.

---

## 1. End-to-End Task Workflow

1. **Check Prerequisites:** Verify in [`TASK_GRAPH.md`](TASK_GRAPH.md) that all `Blocked By` tasks for your `RL-xxx` issue are closed.
2. **Claim the Issue:** Assign the GitHub issue to yourself, move its label from `ready` to `in-progress` (if used), and comment with your branch name and target completion date.
3. **Create a Topic Branch:** Branch from the latest default branch using the naming convention:
   ```bash
   git checkout -b rl/<number>-<short-slug>
   # Example: git checkout -b rl/012-pg-pgvector-verify
   ```
4. **Stay Inside the Boundary ([`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md)):**
   - All files created or modified **must** live inside `research-lab/` (except `.github/workflows/research-lab.yaml` for `RL-005`).
   - Never import from root `app.*`, `evals.*`, or `scripts.*`.
   - Never commit personal names (`Mazen`, `Ehab`, `Elewashy`, `Nexa`) inside `research-lab/` files (`validation/mechanical_checks.py` §5 hard check).
5. **Run Local Verification Before Opening a PR:**
   From `research-lab/`:
   ```bash
   # Always run mechanical checks and Clopper-Pearson check
   python3 validation/mechanical_checks.py \
     Graduation_Project_Proposal.md \
     Methodology_and_Consistency_Checklist.md \
     CHANGELOG.md
   diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt

   # Once RL-003 / RL-006 are merged:
   uv run ruff check .
   uv run pyright
   uv run pytest
   ```
6. **Open a Pull Request:**
   - Title format: `[RL-xxx] <Short imperative summary>`
   - Link the GitHub issue (`Closes #<issue_number>`) and complete `.github/pull_request_template.md` (or `PLAN/templates/pull_request_template.md`).
   - Request review from the designated peer reviewer in [`ASSIGNMENTS.md`](ASSIGNMENTS.md).

---

## 2. Engineering & Research Integrity Rules

- **No fabricated or premature experimental claims:** Never write phrases implying hypotheses have been tested (`"results show"`, `"outperforms"`, `"p < 0.05"`) in specification or code comments prior to Phase `P10` (`validation/mechanical_checks.py` §6).
- **Test-split protection (`Proposal §10.7`):** Development splits are used for all debugging, prompt tuning, judge validation (`RL-033`), and the pilot (`RL-100`). Confirmatory test splits (`splits/*.yaml` frozen in `RL-026`) may be executed **only once** in `RL-101`–`RL-105` after Gates `J5` (`RL-026`), `J7` (`RL-094`), and `J8` (`RL-100`) are signed off. Under V2 these remain in force as gate **G-TS** (`PLAN/V2/DECISIONS_V2.md` `V2-D42`): `scripts/check_test_split_gate.py` enforces J5 and suite coverage in CI, and `evaluation/harness/split_gate.authorize_confirmatory_run` checks integrity before the signed-freeze authorization. G-TS is required before G5, which consumes the `own_P` test split in W5.
- **Update registers in the same PR:**
  - If your task resolves a decision (`D-xx`), commit the decision memo in `research-lab/PLAN/decisions/` and update [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md) and [`../TECHNICAL_SPECIFICATION.md`](../TECHNICAL_SPECIFICATION.md) §2.
  - If your task completes an invariant or §G test row, update [`../TRACEABILITY_MATRIX.md`](../TRACEABILITY_MATRIX.md) in the same PR.
