# ChronaMind — repository guide

Two projects share this repository and share nothing else: separate interpreters, linters, locks, databases, CI jobs and
review standards. **Find out which one you are in before you change anything.** A command or a rule from the wrong one
is the most common way a change here is wrong.

| You are changing | Read | Interpreter | Verify with |
|---|---|---|---|
| `custom-memory-engine/**` — the cognitive memory engine, its schema, its governance | **[`custom-memory-engine/CLAUDE.md`](custom-memory-engine/CLAUDE.md)**, then the documents it names | CPython **3.12** (`custom-memory-engine/.python-version`) | `cd custom-memory-engine && make` targets below |
| `app/**`, `evals/**`, `alembic/**`, `docs/**` — the FastAPI + LangGraph application | **[`AGENTS.md`](AGENTS.md)** (it governs this side only) | CPython **3.14** (`.python-version`) | `make check` |
| `web/**` — the demo interface | `web/README.md`, `tickets/EPIC-demo-interface.md` | Node **22** LTS (`web/.nvmrc`) | `cd web && npm run check` |
| `.github/**` — workflows and Actions | [`AGENTS.md`](AGENTS.md) and [`custom-memory-engine/PLAN/CONTRIBUTING.md`](custom-memory-engine/PLAN/CONTRIBUTING.md) §1.2–1.3 | — | `make check` (runs actionlint) |
| `REVIEW.md`, `CLAUDE.md`, `.claude/**` | this file, plus [`REVIEW.md`](REVIEW.md) | — | `make check` |

`custom-memory-engine/` is excluded from the root Ruff, Pyright and pytest configuration on purpose. Linting it with the
root toolchain runs one Ruff version over files another version formats, so **never** "fix" a file there with the root
command. Its own pinned toolchain is the only correct one for it.

## Commands

```bash
# The FastAPI application (root)
make dev          # hot-reloading server on :8000
make migrate      # Alembic to head
make check        # ruff check + format check + pyright + pytest — the same four gates as CI `checks`

# The memory engine
cd custom-memory-engine
uv run --no-sync pytest -q -m 'not integration'    # seconds, no database; use it while iterating
docker compose --env-file .env -p cme-verify up -d --wait
uv run --env-file .env pytest -q -n auto           # the full suite (what CI `verify` runs)
uv run --no-sync ruff check src tests research/benchmarks evaluation scripts
uv run --no-sync pyright
```

`custom-memory-engine/CLAUDE.md` has the rest: the six static gates, the plan/evidence scripts, and how a new migration
is shaped.

## What is authoritative

1. **`custom-memory-engine/PLAN/V2/` is the approved implementation plan**, and its decision records (`V2-D##` in
   [`DECISIONS_V2.md`](custom-memory-engine/PLAN/V2/DECISIONS_V2.md)) are the binding record of what was decided and why.
   `custom-memory-engine/PLAN/` outside `V2/` and `PROJECT_ROADMAP.md` are the superseded v1 plan: history, not
   instruction.
2. **`Graduation_Project_Proposal.md`, `Methodology_and_Consistency_Checklist.md` and `TECHNICAL_SPECIFICATION.md` §3.3–3.5
   are frozen research records.** They are checksum-bound (`validation/document_hashes.txt`); edit them only through an
   additive header, and never to make a test pass.
3. **`README.md`, `docs/` and the plan files are claims, not proofs.** Where a document and the code disagree, the code
   and its tests are the fact and the document is a defect to report — several of them are already stale (see
   `custom-memory-engine/CLAUDE.md`, "Known drift").
4. **`AGENTS.md` describes the FastAPI template.** Two of its "critical rules" are already false in the code they
   govern (see its scope note). Do not extend them to the engine, and do not enforce them retroactively.

## Ground rules that hold everywhere

- **Never weaken a test to make a suite pass.** Adapt it to a new schema or a renamed symbol; do not delete, skip or
  loosen an assertion. If it fails, find out why first. This has reopened two closed issues in this project already.
- **Never edit a shipped migration, a frozen record or a hash list.** The engine's migration sources are
  checksum-bound; a correction is a new migration or an additive note in `schema/README.md`.
- **Never commit a secret.** `.env*` files are gitignored and hold real passwords. `.claude/settings.json` denies reading
  them.
- **The app holds two engine tokens; never swap them.** `MEMORY_ENGINE_TOKEN` (Tier B) is the agent's.
  `MEMORY_ENGINE_USER_TOKEN` (Tier C, which can erase and export for every user) is used only by the
  `/api/v1/memory/*` routes. Both are secrets in `.env*`, and `.env.example` keeps them empty. See
  [`AGENTS.md`](AGENTS.md), "Memory", and [`docs/memory.md`](docs/memory.md).
- **Commits**: `type(scope): summary`, in the repository's existing style — `feat(e037): … (Refs #207)`,
  `fix(w3): … (#229)`. Reference an issue with `Refs #N`; never `closes`/`fixes` (the issue is closed by hand once its
  criteria are verified).
- **A PR is not "CI green" until every required check reports.** `main` is protected: six required checks, up-to-date
  branches, conversation resolution.
- **Don't merge except at the owner's request or with their approval.** The work is delivered as a pull request;
  merging is the owner's decision.
- **Claude config**: `.claude/settings.json`, `.claude/NOTICE.md` and the repository-owned skills under `.claude/skills/`
  (e.g. `review-pr`) are tracked and shared. Personal overrides go in the gitignored per-user settings file beside them,
  which never ships and must not be cited as if it did.

## Reviewing

Automated review follows [`REVIEW.md`](REVIEW.md) and, for engine changes, `custom-memory-engine/CLAUDE.md`. The review
is advisory to the owner and never a substitute for the deterministic gates: lint, types, tests, the plan scripts and
branch protection decide whether a change is mergeable; review decides whether it is *correct*. See `README.md` in
`.github/workflows/` for how the two reviewers are wired.
