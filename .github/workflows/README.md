# GitHub Actions

| Workflow | Trigger | Jobs (check names) | Purpose |
|---|---|---|---|
| `custom-memory-engine.yaml` | PRs and pushes to `main` touching `custom-memory-engine/**`, this workflow, or `.github/actions/setup-cme/**`; manual | `static`, `verify`, `default-install` (parallel) | Engine quality gates (below) |
| `ci.yaml` | Every PR; pushes to `main`; manual | `checks` | Root application lint, format, type check. No path filter: root `ruff check .` also lints `custom-memory-engine/` |
| `claude-code-review.yml` | Same-repository, non-draft PRs | `claude-review` | Advisory automated review; **not** the peer review required by `custom-memory-engine/PLAN/ASSIGNMENTS.md` |
| `claude.yml` | `@claude` mentions by an owner, member or collaborator | `claude` | On-demand assistant |
| `deploy.yaml` | Manual (`workflow_dispatch`) only | `Build and push to Docker Hub` | Root application image. Automatic publication on `main` is an owner decision and is not enabled |

## Engine quality gates (`custom-memory-engine.yaml`)

| Job | Gates |
|---|---|
| `static` | `ruff check`, `ruff format --check`, `pyright`; frozen research record (`md5sum -c validation/document_hashes.txt`), mechanical checks, Clopper–Pearson bounds, `scripts/check_statistics.py`, G-TS split gate (`scripts/check_test_split_gate.py`) |
| `verify` | Full CI-scope `pytest` (PostgreSQL 16 + pgvector + FalkorDB service containers, prepared model assets): PostgreSQL integration, restricted-role RLS, cross-tenant, migration, bitemporal, deletion and API-contract tests; `validation/bitemporal_checks.sql` |
| `default-install` | `pytest` on the default install with no optional extras |

All three must be green. A PR is not "CI green" because `verify` alone passed.

## Conventions

- **Pinning.** Third-party actions are pinned to a full **commit** SHA with the version in a comment, and service images to a digest. (An annotated-tag object SHA also works but cannot be checked with the commits API; use the commit it dereferences to.)
- **Permissions.** Least privilege: `contents: read` by default; only the Claude workflows add the scopes their action needs.
- **Concurrency.** A new push to a PR cancels that PR's superseded run. Pushes to `main` and deploys are never cancelled.
- **Timeouts.** Every job sets `timeout-minutes`.
- **No duplicate runs.** `push` is limited to `main`; branch work is verified through its PR (or run manually via `workflow_dispatch`).
- **Shared setup.** `.github/actions/setup-cme` is the single place that pins the uv version (0.13.5), the engine Python (3.12.12) and `uv sync --frozen`. The root `ci.yaml` intentionally does not use it: the engine and the root application keep independent CI (`custom-memory-engine/PLAN/INDEPENDENCE_BOUNDARY.md` §4).
- **Secrets.** Never interpolate `${{ secrets.* }}` or event data directly into `run:` scripts; pass them through `env:`.
- **Validation.** `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7` (includes shellcheck) must report nothing.
