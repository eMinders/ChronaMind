# Tickets — Epic: ChronaMind Studio demo interface — Label: ci

Side: `.github/**`, `CLAUDE.md`, `REVIEW.md`, `.claude/**`. Verify with `make check` at the repository root (it runs
actionlint over every workflow).

## References

| Source | Path |
|---|---|
| Parent Epic | [`tickets/EPIC-demo-interface.md`](EPIC-demo-interface.md) |
| Repository guide | [`CLAUDE.md`](../CLAUDE.md), [`REVIEW.md`](../REVIEW.md) |
| Workflow conventions | [`.github/workflows/README.md`](../.github/workflows/README.md); `core/memory-engine/PLAN/CONTRIBUTING.md` §1.2–1.3 |
| Existing root workflow | [`.github/workflows/ci.yaml`](../.github/workflows/ci.yaml) |

---

## [CI-01] · Web workflow and the repository guide row for `apps/studio/`

**Label:** ci, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-01
**Blocks:** CI-03

### Objective

Add `.github/workflows/web.yaml` with one job, `apps/studio`, that runs `npm ci` and `npm run check` in `apps/studio/` on every pull
request and every push to `main`. Add a `apps/studio/**` row to the routing table in `CLAUDE.md`, so every contributor and
agent knows which toolchain verifies the folder.

### Background and Current State

- `.github/workflows/ci.yaml` runs on `pull_request`, `push` to `main` and `workflow_dispatch`, with `permissions:
  contents: read`, a `concurrency` group, and actions pinned by full commit SHA
  (`actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`).
- No workflow uses `actions/setup-node` yet.
- The repository rule: workflows have no `paths:` filter, because a filtered workflow never reports and a required
  check would hang.
- `CLAUDE.md`'s routing table has rows for `core/memory-engine/**`, the app, `.github/**` and the Claude
  configuration, and none for `apps/studio/**`.

### Acceptance Criteria

- `web.yaml` triggers on `pull_request`, `push` to `main` and `workflow_dispatch`, and has no `paths:` or
  `paths-ignore:` filter.
- The job runs on `ubuntu-24.04` with `timeout-minutes: 10`, `permissions: contents: read`, and checkout with
  `persist-credentials: false`.
- Every `uses:` is pinned to a full 40-character commit SHA with a version comment. `actions/checkout` uses the same
  SHA as `ci.yaml`.
- Node comes from `apps/studio/.nvmrc` through `actions/setup-node`, with the npm cache keyed on `apps/studio/package-lock.json`.
- The job runs `npm ci` and `npm run check` with `working-directory: web`, and fails when either fails. The PR shows
  one red run caused by a deliberate lint error, then reverts it.
- `make check` (actionlint) passes with the new workflow.
- `CLAUDE.md` gains the row: ``| `apps/studio/**` — the demo interface | `apps/studio/README.md`, `tickets/EPIC-demo-interface.md` |
  Node **22** LTS (`apps/studio/.nvmrc`) | `cd apps/studio && npm run check` |``.
- Branch protection is not changed. The PR body states that making `apps/studio` a required check is the owner's decision.

### Implementation

```yaml
name: Web

# apps/studio/ (the demo interface): type check, lint, format check and unit tests. No path filter: a skipped workflow never
# reports, and a check that never reports cannot be made required later.

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.sha }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  web:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-node@<full-commit-sha> # <version>; see Needs confirmation
        with:
          node-version-file: apps/studio/.nvmrc
          cache: npm
          cache-dependency-path: apps/studio/package-lock.json
      - name: npm ci
        run: npm ci
      - name: npm run check (typecheck, lint, format, unit tests)
        run: npm run check
```

### Files

```text
.github/workflows/web.yaml    # NEW
CLAUDE.md                     # MODIFIED (routing-table row for apps/studio/**)
```

### Verification

```bash
make check
cd apps/studio && npm ci && npm run check
```

### Dependencies

- Depends on: WEB-01.
- Blocks: CI-03.
- External: **actions/setup-node** (pinned by commit SHA).

---

## [CI-02] · Run the memory router's live tests in the required `app-engine-boundary` job

**Label:** ci, test
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-03
**Blocks:** none

### Objective

Extend the `app-engine-boundary` job in `.github/workflows/ci.yaml` to also run
`apps/api/tests/integration/test_memory_router_live.py`. Every PR then proves the memory router against a real engine host
and PostgreSQL in a required check, and a change to the engine's result shapes that breaks the interface fails CI
before it reaches the web app.

### Background and Current State

- `app-engine-boundary` is a required check. It starts `pgvector/pgvector:0.8.7-pg16-bookworm` (pinned by digest),
  syncs both interpreters, and creates the `vector`, `btree_gist` and `pg_trgm` extensions in `public`.
- Its pytest step runs only `apps/api/tests/integration/test_app_engine_boundary.py` and fails when the output reports any
  skip.
- APP-03 adds `apps/api/tests/integration/test_memory_router_live.py`, with the same skip conditions (engine interpreter,
  `CME_TEST_DATABASE_URL`). APP-04 and APP-05 extend it.

### Acceptance Criteria

- The pytest step runs both files in one invocation, with `-m integration` and `-rs`.
- The step still fails when the output reports any skipped test, for either file.
- The job's `timeout-minutes` covers the longer run. The PR states the measured duration and the new value.
- A deliberate break in `memory_view.py` (episodes placed in `known_facts`) makes this job fail on the PR. The PR
  shows this once, then reverts it.
- `make check` (actionlint) passes.
- No other job, trigger or permission changes.

### Implementation

```yaml
      - name: pytest (app-to-engine boundary and the memory router; a skip is a failure here)
        # Both modules skip without the engine interpreter or the database. Both are provided above, so a skip means
        # the job silently tested nothing: the step fails on it instead of reporting success.
        run: |
          uv run --no-sync pytest -q -rs -m integration \
            apps/api/tests/integration/test_app_engine_boundary.py \
            apps/api/tests/integration/test_memory_router_live.py | tee /tmp/boundary.txt
          if grep -qiE "[0-9]+ skipped" /tmp/boundary.txt; then echo "a boundary test skipped"; exit 1; fi
```

### Files

```text
.github/workflows/ci.yaml     # MODIFIED (app-engine-boundary pytest step and timeout)
```

### Verification

```bash
make check
CME_TEST_DATABASE_URL=<placeholder-dsn> cd apps/api && uv run pytest -q -rs -m integration apps/api/tests/integration/test_app_engine_boundary.py apps/api/tests/integration/test_memory_router_live.py
```

### Dependencies

- Depends on: APP-03 (re-check the timeout after APP-04 and APP-05).
- Blocks: none.
- External: none.

---

## [CI-03] · Web workflow: API type staleness and the mock end-to-end run

**Label:** ci, test
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** CI-01, WEB-02, WEB-13
**Blocks:** none

### Objective

Add two steps to the `apps/studio` job: regenerate `apps/studio/src/api/schema.d.ts` from `apps/api/docs/api/openapi.json` and fail on any
difference, then build the mock bundle and run the Playwright demo spec against it. A backend change that is not
followed by the web types, or a screen change that breaks a journey, fails the PR.

### Background and Current State

- `npm run gen:api` (WEB-02) generates the types from `apps/api/docs/api/openapi.json`. `apps/api/tests/test_openapi_export.py` (APP-07)
  already keeps that document equal to the app.
- `npm run build:mock` and `npm run e2e:mock` (WEB-13) need no backend and no LLM.
- Playwright needs its Chromium build, installed with `npx playwright install --with-deps chromium`.

### Acceptance Criteria

- After `npm ci`, the job runs `npm run gen:api`, then `git diff --exit-code src/api/schema.d.ts`, and fails on a
  difference. The PR shows one red run caused by a hand edit of the generated file, then reverts it.
- The job installs Chromium and runs `npm run build:mock` and `npm run e2e:mock`. It fails when any journey fails.
- On failure, `apps/studio/test-results/` is uploaded as an artifact with `actions/upload-artifact`, pinned by commit SHA,
  with retention of 7 days.
- `timeout-minutes` covers the measured run, and the PR states both numbers.
- The job still has no `paths:` filter, and `make check` (actionlint) passes.

### Implementation

```yaml
      - name: API types match apps/api/docs/api/openapi.json
        run: |
          npm run gen:api
          git diff --exit-code src/api/schema.d.ts
      - name: Install Chromium for Playwright
        run: npx playwright install --with-deps chromium
      - name: Mock build and the demo spec against it (no backend, no LLM)
        run: |
          npm run build:mock
          npm run e2e:mock
      - name: Keep the Playwright report on failure
        if: failure()
        uses: actions/upload-artifact@<full-commit-sha> # <version>; see Needs confirmation
        with:
          name: web-test-results
          path: apps/studio/test-results/
          retention-days: 7
```

### Files

```text
.github/workflows/web.yaml    # MODIFIED
```

### Verification

```bash
make check
cd apps/studio && npm run gen:api && git diff --exit-code src/api/schema.d.ts
cd apps/studio && npm run build:mock && npm run e2e:mock
```

### Dependencies

- Depends on: CI-01, WEB-02, WEB-13.
- Blocks: none.
- External: **actions/upload-artifact** (pinned by commit SHA), Playwright's Chromium.

---

## Needs confirmation

- The full commit SHAs and versions of the current `actions/setup-node` and `actions/upload-artifact` releases.
  Neither is used anywhere in the repository yet. Look them up on the action's release page and pin them the way
  `ci.yaml` pins `actions/checkout`.
- Whether the owner makes `apps/studio` a required check after CI-01 merges.
