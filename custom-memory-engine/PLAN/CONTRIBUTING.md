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
   - Reference the GitHub issue with `Refs #<issue_number>` and close it **manually** once its acceptance criteria
     are verified. Do not put closing keywords (`closes`, `fixes`, `resolves`) in PR bodies or commit messages:
     GitHub closes the issue on merge, even when the PR only partly satisfies it.
   - State the evidence for every acceptance criterion in the PR body (tests run, outputs, commands).
   - Request review from the designated reviewer (see §1.1) — for the engine, that is the AI reviewer.

### 1.1 Review policy

**AI review is the review.** Every change to this engine — code, schema, migration, plan document or
architecture decision — is reviewed by the AI reviewer acting on the Project Owner's behalf before it is
accepted or merged. A human peer review is **not** a prerequisite for merging and is not recorded as one.

What the AI reviewer must actually do, per pull request (a green CI run alone is not review):

1. **Read the code, not the description.** Verify every acceptance criterion against the code and its tests;
   reproduce any claim that matters rather than trusting the PR body.
2. **Probe the assumption that matters.** For a schema or security change, try to break it: the restricted
   runtime role, `search_path` shadowing, cross-tenant references, a trigger disabled, the rename carried
   end to end. Where a guarantee is stated, confirm the test is a genuine control — break the implementation
   and watch the test fail.
3. **Check the sequence, not only the diff.** The r11 W1 review found that four W1 pull requests each carried a
   full copy of `INVARIANT_LEDGER.md` from an older base, so merging them in order silently reverted rows that
   had already advanced, and a later PR's `AUDIT_DETAIL_KEYS` copy dropped a key `main` needed. Any file that
   more than one open branch touches deserves a diff of the *result*, not of each PR.
4. **Reject fixes that weaken a guarantee.** A test may be adapted to a new schema; it may not be deleted,
   skipped or loosened to make CI pass. If it fails, understand why.
5. **Say what is not done.** A partially satisfied issue stays open with its gap written into the ledger, the
   migration header or the issue — never quietly closed.
6. **Record decisions where they belong**: `DECISIONS_V2.md` for architecture, the issue for scope, the ledger
   for guarantees. Do not write a separate audit report to show that a review happened; put the evidence in
   the PR body, the tests and CI.

The reviewer's decisions are recorded as `V2-D##` entries with their rationale and verification, so a human can
confirm, amend or reverse them. Where GitHub's own branch protection would require an approval, that is a
platform setting, not this policy, and no human approval is ever fabricated to satisfy it.

---

## 2. Engineering & Research Integrity Rules

- **No fabricated or premature experimental claims:** Never write phrases implying hypotheses have been tested (`"results show"`, `"outperforms"`, `"p < 0.05"`) in specification or code comments prior to Phase `P10` (`validation/mechanical_checks.py` §6).
- **Test-split protection (`Proposal §10.7`):** Development splits are used for all debugging, prompt tuning, judge validation (`RL-033`), and the pilot (`RL-100`). Confirmatory test splits (`splits/*.yaml` frozen in `RL-026`) may be executed **only once** in `RL-101`–`RL-105` after Gates `J5` (`RL-026`), `J7` (`RL-094`), and `J8` (`RL-100`) are signed off. Under V2 these remain in force as gate **G-TS** (`PLAN/V2/DECISIONS_V2.md` `V2-D42`): `scripts/check_test_split_gate.py` enforces J5 and suite coverage in CI, and `evaluation/harness/split_gate.authorize_confirmatory_run` checks integrity before the signed-freeze authorization. G-TS is required before G5, which consumes the `own_P` test split in W5.
- **Update registers in the same PR:**
  - If your task resolves a decision (`D-xx`), commit the decision memo in `research-lab/PLAN/decisions/` and update [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md) and [`../TECHNICAL_SPECIFICATION.md`](../TECHNICAL_SPECIFICATION.md) §2.
  - If your task completes an invariant or §G test row, update [`../TRACEABILITY_MATRIX.md`](../TRACEABILITY_MATRIX.md) in the same PR.
