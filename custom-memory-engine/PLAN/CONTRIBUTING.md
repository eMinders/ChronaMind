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

   uv run ruff check .
   uv run pyright
   uv run pytest -q -m 'not integration'          # seconds, no database
   uv run --env-file .env pytest -q -n auto       # full suite against the compose database (README §4)
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
   **And read the ledger you are editing.** The r12 stabilization closed `E-011` (#62) and `E-016` (#67) on the
   strength of *passing test names*, then had to reopen both:
   - `E-011`: every scope test passes on a **scratch table** — the suite's own docstring says "It needs no M2
     migration; `E-011` re-runs the cases against the real schema". The catalog showed 0 of 13 tables with a
     scope-policy predicate, and `memory_version` still carrying `user_id = current_setting('app.user_id')`. The
     ledger's `I7` row said exactly that in its gap column; the closure contradicted the row it was updating.
   - `E-016`: 12 of 12 tests passed, but criterion 4 ("each allowed transition without its record raises") is
     unmet — `contested -> asserted` and `contested -> retired` both succeed with no record, because migration 7's
     own header names the missing half ("no table until `E-014` ... nothing here pretends to check them").

   **A green suite is evidence about the tests, not about the criteria.** Read each criterion, find the artefact
   that satisfies it, and confirm that artefact exists on the *migrated* schema — not on a fixture, not on a
   scratch table, not in a test's name. Where a criterion cannot be satisfied yet, leave the issue open even when
   every line of the implementation is correct, and say which criterion is outstanding.
6. **Record decisions where they belong**: `DECISIONS_V2.md` for architecture, the issue for scope, the ledger
   for guarantees. Do not write a separate audit report to show that a review happened; put the evidence in
   the PR body, the tests and CI.

The reviewer's decisions are recorded as `V2-D##` entries with their rationale and verification, so a human can
confirm, amend or reverse them. Where GitHub's own branch protection would require an approval, that is a
platform setting, not this policy, and no human approval is ever fabricated to satisfy it.

### 1.2 Branch protection on `main`

**History.** In r11 the session account had only `write`. After the transfer to `eMinders` (2026-11-05) it was admin, but GitHub's Free plan refused protection and rulesets on a private repository (`403`). The Project Owner made the repository **public** on 2026-11-05, which lifts that restriction.

**Status (2026-11-05): applied and verified.** `PUT /repos/eMinders/ChronaMind/branches/main/protection` succeeded; reading it back returns exactly the table below; `GET /repos/eMinders/ChronaMind/branches/main` reports `protected: true`.
- Each required check is bound to the **GitHub Actions app** (`app_id` 15368), so a commit status posted by anything else cannot satisfy it.
- A root-only probe PR (#174, closed unmerged) showed `changes`/`checks`/`workflows` succeeding and the three engine gates completing as `skipped`, never pending. Its merge state was evaluated, not blocked.
- #172, #173 and #175 then merged through the protection, each brought up to date first.

The configuration is:

| Setting | Value | Why |
|---|---|---|
| Required status checks | `changes`, `static`, `verify`, `default-install` (from `custom-memory-engine.yaml`) and `checks`, `workflows` (from `ci.yaml`) | These are the gates the Definition of Done already names. A PR is not "CI green" on `verify` alone |
| Require branches to be up to date | **on** (`strict: true`) | The r11 review found merge-order regressions (a ledger silently reverted across four branches, #123) that per-PR green CI cannot catch. Strict mode makes a PR's checks run against current `main` before it merges |
| Required approving reviews | **none** | `V2-D57`: AI review is the gate. A required GitHub approval would block every merge or invite an approval that did not happen |
| Require conversation resolution | **on** | A review finding is a comment; this makes "resolve or record it" binding |
| Require linear history | **off** | Merge commits are part of the existing history (`99fe8f8`); squash is the convention, not a rule |
| Allow force pushes / deletions | **off** | Never needed; the v1 tag and every merge commit are the audit trail |
| Enforce for administrators | **off** | The Owner must be able to land an emergency hotfix; the required checks still run on it |

**Why no required check can deadlock a PR.** A workflow skipped by a trigger-level `paths:` filter never reports, so a required check from it would stay pending forever on a root-only PR. `custom-memory-engine.yaml` therefore has **no `paths:` filter on `pull_request`**:
- A `changes` job diffs the PR against its base.
- `static`, `verify` and `default-install` run only when an engine path changed (`custom-memory-engine/**`, the workflow itself, `.github/actions/setup-cme/**`).
- Otherwise they are skipped by their job-level `if:`, and GitHub reports a skipped job as successful.
- `changes` is itself required. If it fails, the PR is blocked, never waved through by the skips that follow it.
- It fails open toward running the gates: a non-PR event or an uncomputable diff runs everything.
- `ci.yaml` has no path filter.

```bash
gh api --method PUT repos/eMinders/ChronaMind/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {"strict": true,
    "checks": [{"context": "changes", "app_id": 15368}, {"context": "static", "app_id": 15368},
               {"context": "verify", "app_id": 15368}, {"context": "default-install", "app_id": 15368},
               {"context": "checks", "app_id": 15368}, {"context": "workflows", "app_id": 15368}]},
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false
}
JSON
gh api repos/eMinders/ChronaMind/branches/main/protection   # read it back
```

**What this does not do.** It does not add a human review requirement, does not weaken any existing gate (there are none
to weaken), and does not make this project's quality depend on a setting that is currently absent. The review standard
is §1.1 and it is enforced by the reviewer, the tests and CI; branch protection is a guard against accidental
force-pushes and stale-base merges, and its absence is recorded here rather than papered over.

---

## 2. Engineering & Research Integrity Rules

- **No fabricated or premature experimental claims:** Never write phrases implying hypotheses have been tested (`"results show"`, `"outperforms"`, `"p < 0.05"`) in specification or code comments prior to Phase `P10` (`validation/mechanical_checks.py` §6).
- **Test-split protection (`Proposal §10.7`):** Development splits are used for all debugging, prompt tuning, judge validation (`RL-033`), and the pilot (`RL-100`). Confirmatory test splits (`splits/*.yaml` frozen in `RL-026`) may be executed **only once** in `RL-101`–`RL-105` after Gates `J5` (`RL-026`), `J7` (`RL-094`), and `J8` (`RL-100`) are signed off. Under V2 these remain in force as gate **G-TS** (`PLAN/V2/DECISIONS_V2.md` `V2-D42`): `scripts/check_test_split_gate.py` enforces J5 and suite coverage in CI, and `evaluation/harness/split_gate.authorize_confirmatory_run` checks integrity before the signed-freeze authorization. G-TS is required before G5, which consumes the `own_P` test split in W5.
- **Update registers in the same PR:**
  - If your task resolves a decision (`D-xx`), commit the decision memo in `research-lab/PLAN/decisions/` and update [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md) and [`../TECHNICAL_SPECIFICATION.md`](../TECHNICAL_SPECIFICATION.md) §2.
  - If your task completes an invariant or §G test row, update [`../TRACEABILITY_MATRIX.md`](../TRACEABILITY_MATRIX.md) in the same PR.
