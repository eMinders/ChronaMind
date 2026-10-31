# W1 Integration, PR Audit, Merging and CI/CD Refactor — Report (r8)

**Date:** 2026-10-31 · **Operator:** maintainer session (`elewashy`, AI-assisted) · **Instruction:** the
Project Owner's request to continue V2 from the verified state of `main`, complete W1 integration,
review and merge ready PRs, close completed issues, and refactor GitHub Actions.

**Summary.** **W1 (`E-010`–`E-017`) was not started**, because its entry gate is not met and the approved plan
forbids starting it (§G). All 11 open PRs were reviewed against their full diffs and linked issues:
nine received `REQUEST_CHANGES` with reproduced findings. The other two (#87, #92) are already on `main`, so
they received comments. **No PR met the merge conditions, so none was merged and no issue was closed.** The
GitHub Actions refactor was delivered, verified on real runners, and keeps every quality gate.

---

## A. Repository state

| Item | Value |
|---|---|
| Starting `main` | `b907125` (r7 head; `origin/main` identical, working tree clean) |
| Commits created | `22636e1` `ci: remove duplicate runs, fix dead triggers, harden and document workflows`; the documentation commit that adds this report (see `SYNC_LOG.md` §13) |
| Push method | Fast-forward to `main`, no force-push. `main` has no branch protection and no rulesets (`GET /branches/main` → `protected: false`); the account has `push`, not `admin` |
| Hooks | The repository's pre-commit hooks are not installed in `.git/hooks`. They were run explicitly on every changed file and all passed, including `detect-secrets` and `check-yaml`. `no-commit-to-branch` was skipped by name, because direct-to-`main` delivery is explicitly authorised |
| Remote verification | `22636e1`: `Custom Memory Engine verification` run 41237550574 (static ✓ 37 s, verify ✓ 135 s, default-install ✓ 44 s) and `CI` run 41237550544 (checks ✓ 22 s), both `push` on `main` |
| Other contributors' branches | Not modified. No commits to any PR branch |

## B. W1 implementation (`E-010`–`E-017`)

**Status of every task: not started in r8, intentionally.** The r7 primitives (B-01–B-09) remain the only
W1-related code on `main`. They are tested but not wired into the engine tables, as `R7_RESOLUTION_REPORT.md` states.

| Task | Issue | State on `main` (verified) | Why not started in r8 |
|---|---|---|---|
| `E-010` M1 axes | #61 | `model.axes` mapping + `V1_BACKFILL_SQL` (proven on 40 inputs); multi-version runner (migrations 1–3). No `kind`/`lifecycle`/`retention` columns | G0b; `MA-016` (#89) unmerged with blocking gaps (`layer` discriminator, `persona` ledger) |
| `E-011` M2+M9 scope/RLS | #62 | `scope.paths.subtree_predicate` (no `LIKE`), `scope.access.read_predicate`, `scope_grant` with forced RLS, restricted role (#47). No `scope_path`/`visibility` on engine tables | G0b; `MA-016` table-coverage verification incomplete; `idempotency_key` oracle (T-13) undecided |
| `E-012` M3 clocks | #63 | Nothing (by design) | G0b; `MA-016` M3 decisions (rename-in-place vs recreate, `observed_at` source) not approved |
| `E-013` M4 SOURCE | #64 | `grounding.sources` (fields + canonical hash) | G0b |
| `E-014` M6 tables | #65 | `enforce_association_cap()` (migration 3), `attach_cap_trigger` | G0b; `API_V2.md` §5.4/§5.5: no `create_entity`/`register_method`, so `ENTITY`/`METHOD` cannot be API-integrated |
| `E-015` admission | #66 | `grounding.sources.ground`, `soundness.admission` (pure) | G0b; depends on E-013/E-014 |
| `E-016` lifecycle | #67 | `model.axes.TRANSITIONS` (pure) | G0b; new conflict N-1 (§I) |
| `E-017` channels | #68 | `scope.provenance` opaque handles (`V2-D37`) | **Hard dependency `MA-013` (#91) not frozen** |

Tests and results: no W1 code was written, so there are no new W1 tests. The regression wall was re-measured (§H).

## C. PR review and merge report

All PRs are by `@Abdulrahman-Ayman-Farid`. Before r8, each PR's only review was the automated `claude` bot's
"No issues found". The reviews below were submitted from `elewashy` and state that they are AI-assisted and do
**not** replace the designated peer review in `PLAN/ASSIGNMENTS.md`. Each finding was reproduced (fixture,
model run, merge onto `main`, or grep), not inferred from the PR text. The author was asked to fix the
blocking items in every `REQUEST_CHANGES` review. No subsequent commits had arrived when this report was written.

| PR | Task | Verdict | Blocking findings (severity) | Merge |
|---|---|---|---|---|
| #82 | E-003 notices | REQUEST_CHANGES | r5 criterion: no notice on `TASK_GRAPH.md`/`ARCHITECTURE.md`, no recorded reason (Low) | Not merged |
| #83 | MA-018 checker | REQUEST_CHANGES | Stale-formula check misses current-use `1 − CP_upper(k, o)` on lines containing "not"/"correctly" (**High**, reproduced); findings lack line numbers (Medium); no retired-alias test (Medium); workflow conflict | Not merged (conflicting) |
| #84 | E-001 status audit | REQUEST_CHANGES | Missing cited path does not exit non-zero (Medium); environment not recorded (Medium); `STATUS_AUDIT.md` stale vs `main` (26/6/3/25 → 28/6/2/24; RL-071 row false) (Medium); CI follow-up not recorded (Low) | Not merged |
| #85 | E-004 invariant ledger | REQUEST_CHANGES | Ledger states facts that are false on `main` ("`security/` is absent", "None of the V2 mechanisms exist") (**High**: E-004's purpose) | Not merged |
| #86 | MA-010 threat model | REQUEST_CHANGES | Categories not analysed: path normalisation, deletion epochs, `audit_event.details` (Medium); stale vs r7 (Medium); conflicts not in `V2-Dnn` form (Low) | Not merged |
| #87 | RL-071 | COMMENT | None. Content on `main` (`3d0f7bb`, `84e504f`, `29e63a3`), #47 closed. Recommended the author close it as superseded | Not merged (conflicting; would be a no-op) |
| #88 | E-002 baseline | REQUEST_CHANGES | Counts measured at `490904d` cannot be reproduced at the proposed tag commit (`main` now 1792 passed) (**High**); tag name/version/commit decisions unrecorded; G3 27 ms P50/P95 discrepancy not flagged; #46/#47 gaps not listed | Not merged |
| #89 | MA-016 migration risk | REQUEST_CHANGES | Missing §3.5 (`persona`), §3.8 (append columns), §3.9 (order), §3.6 verification (**High**: W1 depends on it); decisions not framed as `V2-Dnn` and not reconciled with `V2-D35/38/43`; M3 verification edits `validation/bitemporal_checks.sql` (contradicts r5); `INFERENCE_METHOD`; prose rollbacks | Not merged |
| #90 | MA-014 deletion order | REQUEST_CHANGES | Theorem's "alive" case is false. Reproduced with the PR's own model: `classify(((),(),(0,1),(2,)), {0}, 1, …)` → node 3 `alive` with deleted node 0 in its closure (**High**: proof error, deletion-boundary question); `MAX_DERIVATION_DEPTH` not addressed; cross-scope lock order missing; tests lack scenarios | Not merged |
| #91 | MA-013 channels | REQUEST_CHANGES | Kind-conditioned legality conflicts with `model.axes.TRANSITIONS` (`V2-D38`), so totality fails across documents (**High**); Q5 already decided by `V2-D37`; §2.1–2.3 and the Tier B `contradicts` gap need owner decisions; `audit_only` vs `none` | Not merged |
| #92 | MA-012 API surface | COMMENT | None for the PR: `API_V2.md` byte-identical on `main` (`1230cef`). Recommended closing as superseded. #55 kept open (§D) | Not merged (no-op) |

**Merge conditions.** No PR satisfied conditions 2–3 and 9 (implementation meets requirements; no unresolved
blocking findings; linked issue fully evaluated). CI was green on all 11, which was **not** treated as
sufficient. There are no required checks or required approvals configured on `main`.

## D. Issue status

No issue was closed. Every linked issue was evaluated individually.

| Issue | Acceptance status | Action |
|---|---|---|
| #77 E-003 | 4/5 criteria met by #82; r5 criterion open | Open (PR review) |
| #78 MA-018 | Checker exists in #83; 3 criteria unmet | Open |
| #50 E-001 | Core met in #84; path-exit, environment, CI criteria unmet | Open |
| #52 E-004 | Mechanism met in #85; ledger content inaccurate on `main` | Open |
| #53 MA-010 | Category coverage incomplete | Open |
| #51 E-002 | Tag absent; decisions unrecorded | Open |
| #59 MA-016 | §3.5/3.6/3.8/3.9 missing | Open; W1 blocker |
| #57 MA-014 | Proof error; §3.1/3.6 gaps | Open |
| #56 MA-013 | Not frozen; owner decisions needed | Open; **G0b blocker** |
| #55 MA-012 | On `main`, but criterion 4 (`API_V2.md` does not cite `V2-D39`) and criterion 5 (seven §5 gaps) unmet | Open; comment posted |
| #60 MA-017 | No PR, unclaimed, not implemented | Open; **G0b blocker**; comment posted |
| #61–#68 E-010–E-017 | Not started (§B) | Open; status comment posted on each |
| #46, #47 | Already closed in r7 (verified) | — |

## E. GitHub Actions refactoring

### E.1 Previous inventory (measured from the Actions API and the YAML)

| Workflow | Trigger | Jobs | Runs (last 100) | Defects found |
|---|---|---|---|---|
| `custom-memory-engine.yaml` | `push` (all branches) + `pull_request`, engine paths | `verify` (lint, types, research checks, pytest, bitemporal SQL), `default-install` | 32 | **Every same-repo PR push ran twice** (push + PR: each PR showed `verify` ×2, `default-install` ×2); no concurrency; `setup-uv` pinned to an annotated-tag object, not a commit |
| `ci.yaml` | `pull_request` + `push: [master]` | `checks` | 15, all `pull_request` | **Never ran on `main`** (no `master` branch), so r7's direct pushes were never root-checked; unpinned actions; no `permissions`, timeout or concurrency |
| `deploy.yaml` | `push`/`pull_request` on `master` | `build-and-push` | **0 ever** | Dead triggers; `${{ secrets.* }}` and `${{ github.event.* }}` interpolated into shell; redundant `apt-get install make` |
| `claude-code-review.yml` | every PR event | `claude-review` | 15 | Unpinned; no timeout or concurrency; runs on drafts; would fail on fork PRs (no secret) |
| `claude.yml` | comments, issues, reviews containing `@claude` | `claude` | 38 (all skipped) | Unpinned; no timeout; no author-association guard (relied on the action's own check) |

Baseline durations from the last seven successful engine runs: `verify` 111–173 s (pytest 56–100 s,
container init 20–30 s, pyright 14–28 s), `default-install` 35–47 s. `uv sync` never exceeded 4 s, so
dependency caching would not speed anything up. It was deliberately **not** added, which also avoids a cache-poisoning surface.

### E.2 New architecture

```text
custom-memory-engine.yaml  (PR + push main + manual; engine paths; cancel superseded PR runs)
├── static           ruff, ruff format, pyright, md5 records, mechanical, CP bounds, statistics, G-TS
├── verify           pg16+pgvector+FalkorDB · assets · extensions · full pytest · bitemporal_checks.sql
└── default-install  no-extras pytest                 (all three parallel; all use .github/actions/setup-cme)
ci.yaml                    (every PR + push main + manual)  checks: uv sync --locked, ruff, format, pyright
claude-code-review.yml     (same-repo non-draft PRs; cancel superseded)
claude.yml                 (@claude by OWNER/MEMBER/COLLABORATOR)
deploy.yaml                (manual only)
```

| Change | Reason |
|---|---|
| Engine `push` limited to `main`; `workflow_dispatch` added | Removes the duplicate PR runs. Behaviour change: a branch pushed **without** a PR no longer runs engine CI automatically (use the PR or dispatch) |
| Concurrency on every workflow; cancellation only for `pull_request` events | Superseded PR runs are cancelled; `main` pushes and deploys never are |
| `static` job split from `verify` | Lint/type/research results without waiting for services (37 s vs 111–173 s). Check names `verify` and `default-install` kept, because every issue's Definition of Done cites them |
| Composite action `.github/actions/setup-cme` | One place for uv 0.13.5, Python 3.12.12 and `--frozen` (3 users). Root CI does not use it, keeping D-LAB-CI independence (`INDEPENDENCE_BOUNDARY.md` §4) |
| Root CI → `main`; `uv sync --locked` (lock verified in sync); `permissions: contents: read`; timeout | Fixes the dead push trigger; makes a stale lockfile fail instead of being silently re-resolved |
| All actions pinned to commit SHAs (`checkout` `fbc6f39` v5; `setup-uv` `37802ad` v7.6.0; `claude-code-action` `d535f51` v1) | `94527f2` was the `v7` tag object; it now points to the commit it dereferences to (same code) |
| `deploy.yaml` → manual only; secrets and event data via `env:`; dropped apt step | It never ran. Enabling automatic Docker Hub publication on `main` is an owner decision |
| Claude review skips drafts and fork PRs; `@claude` limited by author association | Secrets are unavailable to forks; defence in depth |

**Nothing was deleted.** No workflow was provably unnecessary: `deploy.yaml` is preserved as a manual
capability, and the two Claude workflows serve distinct purposes.

### E.3 Gates preserved (mechanical check)

A script extracted every executed command from the old and new engine workflow graphs and compared them.
The composite's `uv sync` was expanded, and its argument construction was tested for both modes. Result:
**dropped: none, added: none.** On real runners (`22636e1`): pytest **1792 passed** (`verify`), **1558 passed / 218
skipped** (`default-install`), `ALL ASSERTIONS PASSED` (bitemporal), 5× `OK` (frozen records), `ALL HARD CHECKS
PASSED`, `ALL STATISTICS ASSERTIONS PASSED`, `G-TS integrity PASSED`, ruff clean, pyright 0 errors.

Required status checks: **none are configured** on `main`, so no check identity could be left pending.
Recommendation for an admin: protect `main` and require `static`, `verify`, `default-install` and `checks`.

### E.4 Before/after

| Scenario | Before | After |
|---|---|---|
| Same-repo PR push touching the engine | 2 engine runs = 4 jobs, ≈ 2 × 178 s ≈ 356 job-s; + CI + review | 1 run = 3 jobs, 37 + 135 + 44 = **216 job-s (−39%)**; + CI + review |
| Time to a lint/type/research-record check result | end of `verify` (111–173 s) | **37 s** (`static`) |
| Push to `main` | engine only (root CI never ran) | engine + root CI (+22 s; adds coverage) |

The after figures are from a single run (`22636e1`); the before figures are from seven runs.

### E.5 Not verifiable without a pull request (limitations)

- PR-run deduplication and PR concurrency cancellation: these follow from the YAML and from GitHub's documented
  semantics, but the r8 rules (no new PR for own work) left no PR to exercise them. They will be observable on
  the next push to any open PR.
- The `claude.yml` author-association guard: the 12 skipped runs at 19:49Z predate `22636e1` (20:09Z) and ran
  the old workflow.
- Fork-PR behaviour: the repository is **private**, so fork PRs are rare. The guard is statically correct.
- `actionlint` 1.7.7 (with shellcheck) reports nothing for the old or the new workflows. Its output is
  necessary but not sufficient, which is why the live runs above were required.

## F. Security and integration evidence (unchanged code; re-measured)

| Area | Evidence on `main` | W1 status |
|---|---|---|
| RLS coverage | `FORCE ROW LEVEL SECURITY` on the 12 user tables (11 v1 + `scope_grant`), asserted by `verify_forced_rls` and `test_rls_isolation.py` | V2 tables (`ASSOCIATION`…) do not exist yet (E-014) |
| Restricted role | `NOSUPERUSER NOBYPASSRLS` runtime role tests pass in the 1792 | — |
| Cross-tenant | `test_scope_matching_db.py` (`LIKE` leak reproduced; `starts_with` exact), `test_scope_access_db.py` | Not applied to engine tables (E-011) |
| Provenance privacy | `scope.provenance` per-caller handles, unit and DB tested | Not used by retrieval output (E-017) |
| Deletion/lifecycle | v1 deletion and Invariant 10 tests pass; `model.axes` mapping proven | Not in the schema (E-010/E-016) |

## G. Gates and decisions

- **G0 — not met.** E-001 to E-004 are unmerged (#84, #88, #82, #85), and `v1.0.0` is not tagged (its commit is now an open owner question; see #88 B1).
- **G0b — not met, not declared.** It requires `MA-013` frozen (#91: blocking findings, owner decisions on
  §2.1–2.3 and the Tier B `contradicts` gap) and `MA-017` green (#60: not implemented, unclaimed). `APPROVAL.md`
  Amendment 1 also excludes "starting W1 beyond the blocker primitives".
- **MA-013:** open; see the #91 review. **MA-017:** open; no work exists.
- **Open decisions:** `V2-D14`, `D16`, `D17`, `D30`, `D31`, `D32` and `D34` were not taken. None of them is a W1 entry dependency
  that r8 was authorised to resolve; the new items in §I need the owner.
- **PilotFreeze:** not signed. `check_test_split_gate.py` passes integrity and states that confirmatory runs
  still require a signed PilotFreeze with three distinct human approvals. No sign-off was created or implied.

## H. CI and quality (exact figures, single snapshot `b907125` / `22636e1`)

| Check | Local (disposable pg16 + FalkorDB + assets, Linux) | Remote (`22636e1`) |
|---|---|---|
| Full CI-scope `pytest -q` | 1792 passed, 0 skipped | 1792 passed |
| No-extras, no DB | 1558 passed, 218 skipped (DB/extras-gated) | 1558 passed, 218 skipped |
| ruff check / format (engine) | clean / 207 files formatted | same |
| pyright (engine) | 0 errors | 0 errors |
| Root CI (ruff, format, pyright, py3.13) | clean / 262 formatted / 0 errors | `checks` ✓ |
| `bitemporal_checks.sql` | ALL ASSERTIONS PASSED | same |
| Frozen records `md5sum -c` | 5/5 OK | 5/5 OK |
| Statistics / CP bounds / G-TS | pass / identical / pass | same |
| actionlint 1.7.7 | clean | — |

Clean environments: separate `git worktree`s of `origin/main` for the full and no-extras installs, and a
disposable database container (another session's database was left untouched). An installed-wheel check was
not repeated in r8, because no engine code changed.

## I. Remaining work

| # | Item | Dependency | Owner | Next action |
|---|---|---|---|---|
| 1 | Address review findings on #82–#86, #88–#91 | — | PR author | Push fixes; maintainer re-reviews against the same findings |
| 2 | Close #87 and #92 as superseded | — | PR author / Project Owner | Close; content is on `main` |
| 3 | `MA-017` coverage matrix (#60) | — | unassigned | Claim and implement; it should flag the unowned elements below |
| 4 | **N-1** `CHANNELS.md` kind legality vs `model.axes.TRANSITIONS` (`V2-D38`) | #91, E-016 | Project Owner | `V2-Dnn`: kind-conditioned transitions, or widen legality |
| 5 | **N-2** `ARCHITECTURE_V2.md` §11 `I3` still names `INFERENCE_METHOD` (contradicts §2.1 `METHOD`); #65 says §11 names no table | E-014 | Project Owner | Documentation correction record |
| 6 | **N-3** `API_V2.md` §5.4 `create_entity`, §5.5 `register_method` absent | E-014/E-015 API integration | Project Owner | `V2-Dnn` approving or rejecting the operations |
| 7 | **N-4** Tier B can demote grounded facts via `link(contradicts)` (#91 §2.2) | E-017, API contract | Project Owner | Security decision before E-017 |
| 8 | **N-5** Dependent of a re-derived source keeps content derived from deleted material (#90) | E-037 | Project Owner | Rule or documented exception |
| 9 | **N-6** Which commit is `v1.0.0` (r7 added migrations after the intended freeze point) | #51, G0 | Project Owner | Decide and record in `BASELINE.md`, then tag |
| 10 | `API_V2.md` should cite `V2-D39` (#55 criterion 4) | — | MA-012 owner | One-line documentation fix |
| 11 | Branch protection with required checks | admin rights | repository admin | Require `static`, `verify`, `default-install`, `checks` |
| 12 | Observe PR dedup and cancellation on the next PR push (§E.5) | an open PR | maintainer | Confirm one engine run per push |
| 13 | W1 `E-010`–`E-017` | G0, G0b, items 3–9 | Project Owner authorises | Start only after G0b is declared |

---

## J. Addendum (r8b): review findings resolved and PRs merged (2026-10-31)

**Instruction.** The Project Owner instructed the maintainer session to resolve every PR review finding, push
the changes, merge after verification, and **not to start W1 under any circumstances**.

**Method.** Fixes were added as new commits on top of each author's branch, after merging `main` into it.
Nothing was rebased or force-pushed, and no author commit was rewritten. Each PR was merged only after its
findings were fixed with evidence, CI was green on the exact head (`--match-head-commit`), and the
closing keywords in its body (and, after the near-miss below, its commit messages) matched the intended issue
state. All merges use merge commits per repository convention, except #92 (squash; reason below).

| PR | Fix commit(s) | Merge | Issue |
|---|---|---|---|
| #83 MA-018 checker | `a9d422c`, `415d5fd` (marker vocabulary, line numbers, alias records; 13 tests, 9 fail on the old checker) | `a8079c6` | #78 closed (all criteria) |
| #82 E-003 notices | `799b7e4` (TASK_GRAPH, ARCHITECTURE notices) | `2a9af5c` | #77 closed |
| #84 E-001 audit | `fa8e2c0`, `2d606fd` (missing-path exit, environment, r7 registry, CI `--check`; 29/6/1/24) | `eb7a75a` | #50 closed |
| #85 E-004 ledger | `5d03e15` (r7 reconciliation; 29 cited tests collected and passing) | `4fcbc3c` | #52 **open** (see incident 1) |
| #86 MA-010 threat model | `9c73d44` (T-21..T-23, r7 reconciliation, quoted conflicts) | `88012ac` | #53 closed |
| #89 MA-016 migration risk | `7b1dda7` (decision register D1-D12, M2 measured on PG16, order, persona, rollbacks) | `7795f7d` | #59 closed |
| #88 E-002 baseline | `f13d60c` (Linux reproduction at `490904d`; G3; gaps; open tag decisions) | `9c0f901` | #51 **open**: tag decisions are the owner's |
| #90 MA-014 deletion order | `85e09a4` (proof corrected, rule fixed: old rule 698 counterexamples, new 0; both constants; lock order) | `9013e29` | #57 closed |
| #91 MA-013 channels | `5b12f6e` (legality gap measured and pinned; `V2-D37`; `none`; proposals C-P1..C-P6) | `8180d5e` | #56 **open**: not frozen until C-P1..C-P6 are decided |
| #87 RL-071 | `1cc6fb4` (merge of `main`, all conflicts resolved to `main`; empty net diff) | `9eb78c5` | #47 already closed |
| #92 MA-012 | `f6c861c` (status cites `V2-D39`) | `68f2914` (squash) | #55 **open**: §5 gaps need owner decisions |

**W1 not started (verified).** `git diff b907125 HEAD -- custom-memory-engine/src` is empty: no engine code,
schema or migration changed. The MA-016 measurements ran inside rolled-back transactions on scratch schemas.

**Verification (fresh clone of `f8777a6`).** Full CI scope: **1961 passed, 0 skipped**. No extras: **1727 passed,
218 skipped** (the same 218 environment gates as the baseline). ruff, format and pyright clean; bitemporal `ALL
ASSERTIONS PASSED`; frozen records 5/5; mechanical checks; CP bounds identical; statistics; G-TS integrity;
`check_plan_refs.py` 0 findings; `audit_status.py --check` clean. Remote: every first-parent commit on `main` from
`22636e1` to `f8777a6` (14 commits) has green `CI` and, where engine paths changed, green engine verification.

**Incidents and corrections (recorded deliberately).**
1. **#52 was auto-closed by mistake.** The PR body's `Closes #52` had CRLF line endings, so my edit to `Refs`
   silently did not match, and my merge command did not gate on the closing-keyword count. #52 was reopened
   with an explanation. All later merges went through a guard that refuses unless the keyword count, the
   mergeable state and every check on the exact head are as intended.
2. **Near-miss on #55.** My own commit message in #92 read "closes #55 criterion 4", which would have closed #55
   once the commit reached `main`. #92 was squash-merged with an explicit keyword-free message, so that text never
   reached `main`. Every merged branch was scanned for closing keywords in commit messages before merging.
3. **CI concurrency defect in `22636e1` (mine).** Section E.2 said pushes to `main` are never cancelled. In fact
   GitHub replaces a *pending* run in a shared group even with `cancel-in-progress: false`, so rapid merges
   cancelled runs for `7795f7d` and `9eb78c5`. Fixed in `f8777a6` (main-push groups keyed on the commit SHA);
   both cancelled runs were re-run and passed. E.2's statement is superseded by this paragraph.

**Live CI evidence obtained in this round** (closing §E.5's open items): a PR branch push produced exactly
one engine run (`pull_request` only; #83 at `415d5fd`); same-repo, non-draft PRs ran the Claude review; the
engine workflow correctly did not run on #87, whose diff touches no engine path; the `@claude` author-association
guard evaluated without error on 10 issue-comment events at `bb67cf7` (all correctly skipped).

**Remaining for the Project Owner** (unchanged in kind; now concrete):
- #51: choose the `v1.0.0` commit, tag name and version (`BASELINE.md` §7.1; recommendation `490904d`, `cme-v1.0.0`).
- #52: confirm that criterion 3 is superseded by the closure of #46/#47, then close.
- #55: decide the seven `API_V2.md` §5 gaps (`create_entity`, `register_method` block W1 API integration).
- #56 / G0b: decide C-P1..C-P6 in `CHANNELS.md` §5 (C-P4 is a security item); only then is MA-013 frozen.
- #59 follow-ups: proposed decisions D6-D12 in `MIGRATION_RISK.md` §1.1 before `E-010`-`E-012`.
- #57 follow-up: confirm the corrected transitive re-derivation rule and the cross-scope lock-order rule.
- MA-017 (#60): still unclaimed; G0b also needs it green. **W1 remains not started.**

> **r9 (2026-11-01).** Every item under "Remaining for the Project Owner" above was decided or completed;
> see `R9_PRE_W1_COMPLETION_REPORT.md`. This report is otherwise kept as written.
