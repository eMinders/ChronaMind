# GitHub Actions

| Workflow | Trigger | Jobs (check names) | Purpose |
|---|---|---|---|
| `custom-memory-engine.yaml` | Every PR (a `changes` job skips the gates when neither `custom-memory-engine/` nor `.github/` changed, so required checks still report; `.github/` counts because the engine suite tests the CI configuration); pushes to `main` touching those paths; manual | `changes`, then `static`, `verify`, `default-install` (parallel), plus advisory `dual-profile` | Engine quality gates (below) |
| `ci.yaml` | Every PR; pushes to `main`; manual | `checks`, `workflows` | `checks`: root application lint, format, type check and tests (`tests/`, no services). The engine is excluded from the root Ruff config and verified only by its own workflow. `workflows`: actionlint over every workflow, pinned by digest |
| `claude-code-review.yml` | Same-repository, non-draft, non-Dependabot PRs, on open, push, ready and reopen | `claude-review` | Advisory automated review, through the repository's own `review-pr` skill (below), not the stock `code-review` plugin. A gate step decides the tier from the PR's history and skips a push that changed no tree before the action is paid for. A second pair of eyes, not the review gate: the gate is the AI review on the Project Owner's behalf (`V2-D57`, `PLAN/CONTRIBUTING.md` §1.1), and no human peer review is a prerequisite |
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

Four things it decides before the model is paid for, or verifies after GitHub answers — the four the canary got wrong:

| | Mechanism |
|---|---|
| **Identity** | `REPOSITORY` is passed in and is the only source of a URL (§1 of the skill). The canary cited a different owner, recalled rather than read. |
| **Tier** | `REVIEW_TIER` is read from the pull request's review history by the gate step, not inferred from the diff. A base branch that advances moves the diff range; the canary's second review then called itself a first review and left its own thread open. |
| **Resolution** | the skill fetches threads by GraphQL, matches them by comment content, and resolves one only after verifying the fix in the code — and only when `isResolved: true` comes back. The canary verified the fix and left the thread open. |
| **Cost** | the gate compares the pushed tree with the one the last review read and skips the run when they are identical. The canary's empty commit bought a 15-turn run that posted nothing. |

Each is a line of `.github/scripts/review_gate.py` or of the skill, and each has a test in
`custom-memory-engine/tests/test_review_gate.py` or `tests/test_claude_integration.py`.

What it can and cannot do: it reads the diff and the surrounding code, and it does not build or run the repository. It
never approves, never merges and never pushes — those tools are denied in `claude_args` — so it cannot satisfy a branch
protection requirement or substitute for a human decision. It complements the deterministic gates; it does not replace
them, and a clean review is not proof of correctness.

**The one write it performs** is resolving its own review threads, and only after verifying the fix and getting
`isResolved: true` back. No `permissions:` change is involved: the action mints an App token by OIDC exchange, and that
installation — not the job's `GITHUB_TOKEN`, which stays read-only — is the identity that already posts the comments.

What that identity needs is more specific than it looks: `resolveReviewThread` is gated on **Contents: write**, not on
`pull_requests: write` (a token holding only the latter is refused with `Resource not accessible by integration`; see
`github/gh-aw#35726`, `oven-sh/bun#36959` and the note in the `zuke-build/zuke#634` thread). The action's default token
request already asks for `contents: write`, `pull_requests: write` and `issues: write`
(`src/github/token.ts` at the pinned SHA), so this repository's installation should already qualify. The skill does not
rely on that: it reads `viewerCanResolve` first and, when it is false, reports `thread open: not permitted (no Contents:
write)` and leaves the thread alone. **If resolution fails in practice**, the manual action is on the App installation,
not in this repository: the Claude GitHub App (app settings → Permissions & events) must grant
*Contents: Read and write* for the installing account, and the installation must include this repository.

**A pull request that edits `claude-code-review.yml` (or `claude.yml`) gets no automatic review, and its
`claude-review` check is green.** That is not a policy here but a platform rule: the action's OIDC exchange is refused
unless the running workflow file is byte-identical to the default branch's, so the action exits "skipped" and the job
passes. Nothing in this repository can lift it, and buying around it by weakening the check is not an option. So the
gate prints a warning and a step-summary notice naming the workflow, and the review is requested explicitly with a
`@claude review` comment: that job runs on `issue_comment`, whose workflow *is* the default branch's, and reviews any
pull request including the one that changes the workflow.

**Cost.** Each run is billed to the subscription behind `CLAUDE_CODE_OAUTH_TOKEN` and consumes GitHub Actions
minutes. Three things bound it, none of them a model downgrade:

- **A push that changes no tree is not reviewed.** The gate step compares the pushed commit with the head the last review
  recorded in its summary, and skips before the action mints a token. It does not filter by path: a change to `PLAN/V2/`,
  `.github/` or an instruction file is reviewable here, so any tree change is reviewed and an allow list would be the way
  a governance change gets skipped as a docs change. A skipped run says so in the step summary, with the sha it compared.
- **A superseded review is cancelled**, by a concurrency group keyed on the PR, and every job has a timeout. A run
  started by a bot lands in a group of its own: the action posts an acknowledgement comment as it starts, and that
  comment used to re-enter the same concurrency group and cancel the run that had just posted it.
- **The model is a repository variable**, `CLAUDE_REVIEW_MODEL` (default `claude-sonnet-5-5`; `claude-opus-5-5` buys
  depth at a materially higher cost per review).

The run log's last line reports `num_turns` and `total_cost_usd` for every review, so the per-PR cost is measured rather
than guessed — and those are API-equivalent figures for a subscription, not a quota or a bill.

**Duplicate reviews.** The managed **Claude Code Review** service (Team/Enterprise, enabled by a Claude organization
Owner) is a different, deeper product with a check run, inline findings and its own spend limit. If it is enabled for
this repository, set the repository variable **`CLAUDE_REVIEW_MODE=native`** and the Action job skips before minting a
token. Nothing in this repository can read or change that setting, so it is an administrator action, not a file.

**Where the baseline comes from.** The gate decides "is this push worth a review?" from two things, in this order,
because the first is exact and the second cannot be broken by anything the model writes:

1. **The previous summary comment**: `Base:` and `Head:` as markdown fields, or the same two names inside a sentence. The
   loose form exists because the first live run of the finished reviewer posted its summary as a single flattened
   paragraph — no heading, no bold — after which the strict parse reported *no earlier review*: a re-review announced
   itself as a first review (the #231 failure, reintroduced by formatting) and no empty push could ever be skipped.
2. **GitHub's timeline** (`GET /issues/{n}/timeline`): `committed` events carry every pushed sha, `reviewed` events carry
   the `commit_id` the review was submitted against. The reviewer does not write these.

The timeline recovers the **tier** but never the base commit, and a baseline with no base licenses no skip — a base that
moved changes what the pull request means even when its own tree did not. So a timeline-only baseline reviews and says
`history_incomplete` while doing it. Conservative on purpose: the alternative to a decision is a review, never a skip.

Replay it read-only against any pull request:

```bash
REPOSITORY=eMinders/ChronaMind PR_NUMBER=235 HEAD_SHA=<sha> BASE_SHA=<sha> ACTION=synchronize \
  GH_TOKEN=$(gh auth token) python3 .github/scripts/review_gate.py
```

**Verifying the reviewer.** A green run proves nothing about detection. After any change to `REVIEW.md`, the skill or
the workflow, open a throwaway PR with a planted defect (delete a guard from a migration trigger, bypass `assemble` on a
read path) and confirm the review reports it inline and cites the mechanism. The gate can be replayed read-only against
any past PR, which is how its tier and skip decisions were checked without a live run:

```bash
REPOSITORY=eMinders/ChronaMind PR_NUMBER=<n> HEAD_SHA=<sha> BASE_SHA=<sha> ACTION=synchronize \
  python3 .github/scripts/review_gate.py
```

**A controlled canary, and the rules that keep it safe.** Detection is the one property a green run cannot show, so it is
tested by planting a defect. This is the procedure the reviewer was last validated with, and it is the only thing that
proves a change to `REVIEW.md`, the skill or this workflow has not cost a finding:

1. Branch from `main` and plant **one** semantic defect tied to a real invariant, of the kind CI cannot see — no syntax
   error, no failing test, no lint. Verify that by mutation first: with the defect in place the full suite and every
   static gate must still pass, or the canary tests CI rather than the reviewer.
2. Write the expected finding down **before** pushing — location, trigger, wrong behaviour, consequence, the safeguard
   that misses it, the fix, and the severity you expect — and keep it outside the repository. A record written
   afterwards only describes what happened.
3. Push the defect only, and open a normal pull request marked do-not-merge. It must not touch `.github/workflows/`,
   `REVIEW.md` or `.claude/`: a change to those is exactly what the platform will not review (above), so such a canary
   would test nothing.
4. Compare against the record: was it detected, at the right location, with the right mechanism and a calibrated
   severity? Then push the fix and check the *re-review* — `Tier: re-review`, the finding reported fixed, its thread
   resolved, and nothing repeated.
5. Close it unmerged and delete the branch. A canary is evidence about the reviewer and is never a change: it must not
   reach `main`, and it must not leave a defect behind. The instruction files' own references are
checked on every run: `tests/test_claude_integration.py` holds them to `scripts/check_evidence.py`, and pins the
workflow's triggers, permissions, deny list and base-branch checkout.

**Why `@chronamind`, not `@claude`.** One comment must start one agent. `@claude review` is the reviewer, in both the
managed service and `claude.yml`; the interactive assistant has its own phrase so a plain `@claude` in prose never
starts two runs. The assistant can implement a change on a branch but is denied `git push` to a protected one, merge,
approve and `gh api --method PUT`.
