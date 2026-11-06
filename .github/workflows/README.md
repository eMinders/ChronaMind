# GitHub Actions

| Workflow | Trigger | Jobs (check names) | Purpose |
|---|---|---|---|
| `custom-memory-engine.yaml` | Every PR (a `changes` job skips the gates when no engine path changed, so required checks still report); pushes to `main` touching `custom-memory-engine/**`, this workflow, or `.github/actions/setup-cme/**`; manual | `changes`, then `static`, `verify`, `default-install` (parallel) | Engine quality gates (below) |
| `ci.yaml` | Every PR; pushes to `main`; manual | `checks`, `workflows` | `checks`: root application lint, format, type check and tests (`tests/`, no services). The engine is excluded from the root Ruff config and verified only by its own workflow. `workflows`: actionlint over every workflow, pinned by digest |
| `claude-code-review.yml` | Same-repository, non-draft PRs | `claude-review` | Advisory automated review: a second pair of eyes, not the review gate. The gate is the AI review on the Project Owner's behalf (`V2-D57`, `PLAN/CONTRIBUTING.md` §1.1); no human peer review is a prerequisite |
| `claude.yml` | `@claude` mentions by an owner, member or collaborator | `claude` | On-demand assistant |
| `deploy.yaml` | Manual (`workflow_dispatch`) only | `Build and push to Docker Hub` | Root application image. Automatic publication on `main` is an owner decision and is not enabled |

## Engine quality gates (`custom-memory-engine.yaml`)

| Job | Gates |
|---|---|
| `static` | `ruff check`, `ruff format --check`, `pyright` (including `scripts/`); frozen research record (`md5sum -c validation/document_hashes.txt`), mechanical checks, Clopper–Pearson bounds, `scripts/check_statistics.py`, G-TS split gate (`scripts/check_test_split_gate.py`), plan references (`check_plan_refs.py`), status registry (`audit_status.py --check`), architecture ownership (`plan_coverage.py --check`) |
| `verify` | Full CI-scope `pytest -n auto` with work stealing: one disposable database per xdist worker (`conftest.py`, #167) and a private clone of a runner-migrated template per database test (`tests/integration/schema.py`), on a PostgreSQL service with durability switched off (throwaway data) plus FalkorDB and prepared model assets: PostgreSQL integration, restricted-role RLS, cross-tenant, migration, bitemporal, deletion and API-contract tests; `validation/bitemporal_checks.sql` |
| `default-install` | `pytest` on the default install with no optional extras |

All three must be green (or skipped by `changes` when no engine path changed). A PR is not "CI green" because `verify` alone passed.

`main` is **protected** (applied and verified 2026-11-05): six required checks bound to GitHub Actions, up-to-date branches, conversation resolution, no force pushes or deletions. Details and the verification are in `custom-memory-engine/PLAN/CONTRIBUTING.md` §1.2, including why no required check can leave a PR pending forever.

## Conventions

- **Pinning.** Third-party actions are pinned to a full **commit** SHA with the version in a comment, and service images to a digest. (An annotated-tag object SHA also works but cannot be checked with the commits API; use the commit it dereferences to.)
- **Permissions.** Least privilege: `contents: read` by default; only the Claude workflows add the scopes their action needs.
- **Concurrency.** A new push to a PR cancels that PR's superseded run. Each push to `main` has its own group (keyed on the commit SHA), so no `main` run is cancelled or replaced: GitHub replaces a *pending* run in a shared group even when `cancel-in-progress` is false. A running deploy is never cancelled; a newer manual dispatch supersedes one that is still queued.
- **Timeouts.** Every job sets `timeout-minutes`.
- **No duplicate runs.** `push` is limited to `main`; branch work is verified through its PR (or run manually via `workflow_dispatch`).
- **Shared setup.** `.github/actions/setup-cme` is the single place that pins the uv version (0.13.5), the engine Python (3.12.12) and `uv sync --frozen`. The root `ci.yaml` intentionally does not use it: the engine and the root application keep independent CI (`custom-memory-engine/PLAN/INDEPENDENCE_BOUNDARY.md` §4).
- **Secrets.** Never interpolate `${{ secrets.* }}` or event data directly into `run:` scripts; pass them through `env:`.
- **Validation.** `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7` (includes shellcheck) must report nothing.
