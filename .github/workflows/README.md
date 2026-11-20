# GitHub Actions

| Workflow | Trigger | Jobs (check names) | Purpose |
|---|---|---|---|
| `custom-memory-engine.yaml` | Every PR (a `changes` job skips the gates when neither `custom-memory-engine/` nor `.github/` changed, so required checks still report; `.github/` counts because the engine suite tests the CI configuration); pushes to `main` touching those paths; manual | `changes`, then `static`, `verify`, `default-install` (parallel), plus advisory `dual-profile` | Engine quality gates (below) |
| `ci.yaml` | Every PR; pushes to `main`; manual | `checks`, `workflows` | `checks`: root application lint, format, type check and tests (`tests/`, no services). The engine is excluded from the root Ruff config and verified only by its own workflow. `workflows`: actionlint over every workflow, pinned by digest |
| `claude-code-review.yml` | Same-repository, non-draft, non-Dependabot PRs, on open, push, ready and reopen | `claude-review` | Advisory automated review, through the repository's own `review-pr` skill (below), not the stock `code-review` plugin. A second pair of eyes, not the review gate: the gate is the AI review on the Project Owner's behalf (`V2-D57`, `PLAN/CONTRIBUTING.md` §1.1), and no human peer review is a prerequisite |
| `claude.yml` | `@chronamind` in an issue or PR comment, or `@claude review` on a PR, by an owner, member or collaborator | `claude` (assistant), `review` (reviewer) | The interactive assistant, and an on-demand review through the same `review-pr` skill |
| `app-image.yaml` | PRs and `main` pushes touching anything that goes into the root image (`Dockerfile`, lock, `app/`, ...); manual | `image` | Builds the root image, imports the app inside it, checks it runs as `appuser` with read-only code, reports its size. Not required, so its path filter is safe |
| `deploy.yaml` | Manual (`workflow_dispatch`) only | `Build and push to Docker Hub` | Root application image. Automatic publication on `main` is an owner decision and is not enabled |

## Engine quality gates (`custom-memory-engine.yaml`)

| Job | Gates |
|---|---|
| `static` | `ruff check`, `ruff format --check`, `pyright` (including `scripts/`); frozen research record (`md5sum -c validation/document_hashes.txt`), mechanical checks, Clopper–Pearson bounds, `scripts/check_statistics.py`, G-TS split gate (`scripts/check_test_split_gate.py`), plan references (`check_plan_refs.py`), status registry (`audit_status.py --check`), architecture ownership (`plan_coverage.py --check`) |
| `verify` | Full CI-scope `pytest -n auto` with work stealing: one disposable database per xdist worker (`conftest.py`, #167) and a private clone of a runner-migrated template per database test (`tests/integration/schema.py`), on a PostgreSQL service with durability switched off (throwaway data) plus FalkorDB and prepared model assets: PostgreSQL integration, restricted-role RLS, cross-tenant, migration, bitemporal, deletion and API-contract tests; `validation/bitemporal_checks.sql` |
| `default-install` | `pytest` on the default install with no optional extras |
| `dual-profile` | One PostgreSQL service, migrated **once** through `schema.migrate` with the applied state gated, then both profiles on that one database: the conversational adapter suite, the coding-profile suite, the cross-profile parity and isolation module (`tests/integration/test_dual_profile_db.py`: forbidden both ways, RLS-indistinguishable, both orders, exposure 0 with its one-sided 97.5% Clopper–Pearson bound) and the restricted-role Suite I bound. Each suite clones the migrated database (`CME_TEST_PREMIGRATED_DATABASE`), so one migration invocation appears in the log |

All three required gates must be green (or skipped by `changes` when no engine path changed). `dual-profile` is advisory: it reports but does not block a merge until it is added to the required checks (`custom-memory-engine/PLAN/CONTRIBUTING.md` §1.2). A PR is not "CI green" because `verify` alone passed.

`main` is **protected** (applied and verified 2026-11-05): six required checks bound to GitHub Actions, up-to-date branches, conversation resolution, no force pushes or deletions. Details and the verification are in `custom-memory-engine/PLAN/CONTRIBUTING.md` §1.2, including why no required check can leave a PR pending forever.

## Conventions

- **Allowed actions.** Only GitHub-owned actions and an explicit allow list may run, and every reference must be SHA-pinned; both are enforced by repository settings (`custom-memory-engine/PLAN/CONTRIBUTING.md` §1.3). A new third-party action must be added to the allow list first.
- **Pinning.** Third-party actions are pinned to a full **commit** SHA with the version in a comment, and container images to a digest. (An annotated-tag object SHA also works but cannot be checked with the commits API; use the commit it dereferences to.)
- **Updates.** `.github/dependabot.yml` proposes weekly grouped updates for actions (including `setup-cme`), both uv projects and the root image, after a 7-day cooldown. The engine's evaluated baseline vendors are ignored: they are frozen research inputs (`evidence/RL-031-verification.md`).
- **Permissions.** Least privilege: `contents: read` by default; only the Claude workflows add the scopes their action needs. Checkouts set `persist-credentials: false`, since no job uses git credentials after cloning. The Claude action manages its own authentication.
- **Concurrency.** A new push to a PR cancels that PR's superseded run. Each push to `main` has its own group (keyed on the commit SHA), so no `main` run is cancelled or replaced: GitHub replaces a *pending* run in a shared group even when `cancel-in-progress` is false. A running deploy is never cancelled; a newer manual dispatch supersedes one that is still queued.
- **Timeouts.** Every job sets `timeout-minutes`, sized to a few times its measured duration so a hang fails fast.
- **No duplicate runs.** `push` is limited to `main`; branch work is verified through its PR (or run manually via `workflow_dispatch`).
- **Python and uv.** Each project's interpreter comes from its own `.python-version` (root: 3.14; engine: the CPython 3.12 line, `RL-013`), so CI and local runs agree. uv is pinned in two places, `ci.yaml` and `.github/actions/setup-cme`, because the root and engine keep independent CI (`custom-memory-engine/PLAN/INDEPENDENCE_BOUNDARY.md` §4). Each keys its uv cache on its own lock only.
- **Lint scope.** Each project is linted once, by its own pinned Ruff and Pyright: the root config excludes `custom-memory-engine/`.
- **Secrets.** Never interpolate `${{ secrets.* }}` or event data directly into `run:` scripts; pass them through `env:`.
- **Validation.** `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12` (includes shellcheck) must report nothing.

## The Claude review

`.github/workflows/claude-code-review.yml` runs `.claude/skills/review-pr/SKILL.md`, a procedure this repository owns,
rather than the stock `code-review` plugin. The plugin was measured on this repository: across 106 pull requests it
posted 107 summary comments, every one of them "No issues found", and four inline comments — because its own instructions stop a review as soon as
Claude has commented on a PR and restrict its bug agents to the diff. The defects it was meant to catch (`#228`'s
`SECURITY DEFINER` gate on a forgeable GUC, `#229`'s `get` bypassing `assemble`) were found by hand afterwards.

The replacement differs in four ways that matter here:

| | Stock plugin | `review-pr` |
|---|---|---|
| Instructions | plugin text, same for every repository | `REVIEW.md` and `custom-memory-engine/CLAUDE.md`, **read from the base branch** so a PR cannot edit the standard it is reviewed against |
| Scope | the diff | the diff plus the callers, the SQL/Python twins, the invariant ledger, the threat model and the migration list |
| A second push | skipped if Claude has already commented | reviewed, strictly: new regressions and unfixed earlier findings |
| Findings | high-signal only | evidence bar of six parts, severity-ranked, nit-capped, deduplicated |

What it can and cannot do: it reads the diff and the surrounding code, and it does not build or run the repository. It
never approves, never merges and never pushes — those tools are denied in `claude_args` — so it cannot satisfy a branch
protection requirement or substitute for a human decision. It complements the deterministic gates; it does not replace
them, and a clean review is not proof of correctness.

**Cost.** Each run is billed to the subscription behind `CLAUDE_CODE_OAUTH_TOKEN` and consumes GitHub Actions
minutes. The two levers are repository variables, not edits to the workflow: `CLAUDE_REVIEW_MODEL` (default
`claude-sonnet-5-5`; `claude-opus-5-5` buys depth at a materially higher cost per review) and, indirectly, how often it runs — it
reviews every push, because a PR here is rebased several times and the merged tree is what ships. The run log's last
line reports `num_turns` and `total_cost_usd` for each review, so the per-PR cost is measured, not guessed. A concurrency
group keyed on the PR cancels a superseded review, and every job has a timeout, so a stuck run cannot bill for long.

**Duplicate reviews.** The managed **Claude Code Review** service (Team/Enterprise, enabled by a Claude organization
Owner) is a different, deeper product with a check run, inline findings and its own spend limit. If it is enabled for
this repository, set the repository variable **`CLAUDE_REVIEW_MODE=native`** and the Action job skips before minting a
token. Nothing in this repository can read or change that setting, so it is an administrator action, not a file.

**Verifying the reviewer.** A green run proves nothing about detection. After any change to `REVIEW.md`, the skill or
the workflow, open a throwaway PR with a planted defect (delete a guard from a migration trigger, bypass `assemble` on a
read path) and confirm the review reports it inline and cites the mechanism. The instruction files' own references are
checked on every run: `tests/test_claude_integration.py` holds them to `scripts/check_evidence.py`, and pins the
workflow's triggers, permissions, deny list and base-branch checkout.

**Why `@chronamind`, not `@claude`.** One comment must start one agent. `@claude review` is the reviewer, in both the
managed service and `claude.yml`; the interactive assistant has its own phrase so a plain `@claude` in prose never
starts two runs. The assistant can implement a change on a branch but is denied `git push` to a protected one, merge,
approve and `gh api --method PUT`.
