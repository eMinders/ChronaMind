# Independent review of migration 33 and the restore-reconciliation gate (`T-46`, `V2-D125`)

**Issue:** `#264` · **Reviewed tree:** `main` at `4cd483b` (the head this record was written against), whose
`custom-memory-engine/src`, `scripts/` and `deploy/` are byte-identical to `862e7d5`, the commit that carries the merge
`321f108` and the tracking commits after it — verified with `git diff 862e7d5 4cd483b -- custom-memory-engine/src
custom-memory-engine/scripts custom-memory-engine/deploy .github` (empty) · **Changed by this record:** nothing under
review; this is the review, and the one tooling change it needed lives in `.github/`

**Why here.** `evidence/` is where this repository keeps written review and verification records — `VERIFICATION.md`,
`POSTMERGE-assurance.md`, `RL-0NN-verification.md` — and `reports/` is the graduation-project research record (its
`README.md` scopes it to H1–H4). A review of merged code is the `POSTMERGE-assurance.md` shape: a base commit, a table of
claim → direct verification, and what the record could not verify.

**Why it exists at all.** `#260` merged with a caveat: its final head (`7c4ad14`) was never reviewed, because every
attempt of the review job failed with no message. The three things this record settles are therefore (1) what that
failure was and when it ended, (2) a fresh-eyes review of the merged whole, and (3) where each thing it found was
filed.

## 1. The review tooling failure

**It was not the `tsconfig.json` line.** Every log of the job contains

```
# ("Internal error: directory mismatch for directory .../tsconfig.json")
```

and it is a **comment** in `anthropics/claude-code-action`'s own `run:` block —

```yaml
run: |
  # Do NOT pass --tsconfig-override here. It triggers a Bun runtime bug
  # ("Internal error: directory mismatch for directory .../tsconfig.json")
  # that aborts the run with exit code 1. ...
  bun --no-env-file --config=... run .../run.ts
```

— echoed by the runner's `##[group]Run` header at `15:21:24.3636840Z`, before anything executes, at **line 422 of the
attempt-3 job log**. It is line 422 of the **green** job log `45845609418` too (`is_error: false`, nine turns,
`$0.138296…`), which is what rules it out as the cause. Both jobs ran the same action commit (`4d75e5f`), the same CLI
(`Claude Code v2.1.323`), the same runner image (`Version: 20261111.635`) and a byte-identical `SDK options` block
(`diff` of log lines 619-671 is empty), fifteen minutes apart.

**What actually failed.** The run died before its first answer:

```json
{"type": "result", "subtype": "success", "is_error": true, "duration_ms": 377,
 "num_turns": 1, "total_cost_usd": 0, "permission_denials_count": 0, "modelUsage": {}}
```

`num_turns: 1`, `total_cost_usd: 0` and an empty `modelUsage` mean no tokens were billed: the CLI produced an error
result instead of a completion, and the action turns `is_error: true` into a failed step. The message itself was never
printed — `show_full_output` is false — so the repository cannot name the cause from its own logs, only its class.
Content is excluded: run `45852260361` failed identically at `15:46:11Z` on a **docs-only** pull request (`#265`, two
markdown files), and the last green run reviewed a tree that differs from `7c4ad14` by three lines of prose.

**It was transient, and it is over.** The three attempts of `45846436962` failed identically (jobs `140001212839`,
`140004564426`, `140007340635`, started `15:10:45Z`, `15:16:27Z`, `15:21:12Z`, each one turn, no cost, 383 / 399 / 377
ms), and so did `45852260361`. Then, **with no change to the workflow, the action pin or `.claude/`** —
`git log origin/main -- .github/workflows/claude-code-review.yml` ends at `d30125e`, long before — the job went green
again: `45884428602` (result at `19:34:00Z`, head `fb3f69b`, `is_error: false`, `num_turns: 17`, `$0.3243538`) and
`46022231832` (2026-11-27, `is_error: false`, the same action pin `4d75e5f` and the same CLI `v2.1.323`). So the
failure began after the last green result (`15:06:48Z`) and by the first red one (`15:11:29Z`), and was over by
`19:34:00Z` on 2026-11-26 — a window of at most four and a half hours, on the credential/model side of the runner.

**Conclusion, stated plainly.** The failure is not in this repository and needs no workaround: the review job reviews
again, and the acceptance criterion "the next push is reviewed rather than skipped" is met by measurement. What the
repository can say about the *cause* is a **hypothesis, labelled as one**: with one turn and nothing billed, the first
call to the model failed before answering. That is the shape of a credential that is invalid, expired, revoked or over
its limit (`secrets.CLAUDE_CODE_OAUTH_TOKEN`), or of a model id the account cannot use — and a provider-side failure
looks the same from here. Which it was, and whether an operator re-issued the token or the condition simply cleared, is
**not recoverable** from the repository's own logs, because the run's message is hidden (`show_full_output: false`) and
nothing in git changed in between. That is precisely the gap the one fix here closes for the next failure.

**The reviews this record still owes.** The recovery came after `7c4ad14` was merged: the green runs of that evening
and the next day are reviews of other branches (`fix/261-…`, `fix/263-…`), not of `321f108`. No automated review of the
merged T-46 tree exists, so this record *is* the substitute — a hand-run review against
`.claude/skills/review-pr/SKILL.md`, recorded on the issue.

**The one thing that was fixable here.** The reason this issue could be filed with a wrong root cause is that a failed
run said nothing. `.github/scripts/review_failure_report.py` now prints the run's own `result` fields on failure — the
error text among them, capped at 300 characters, and nothing from `messages`, `content` or the prompt — and
`.github/workflows/claude-code-review.yml` runs it only when the review step itself failed. Pinned by
`tests/test_claude_integration.py::test_a_failed_review_reports_why_with_the_error_text_and_not_the_transcript`,
`::test_the_report_names_the_failure_and_never_quotes_the_run` and
`::test_the_report_never_claims_a_cause_it_cannot_see`.

What the step must never do, and what holds it there:

- **Publish the transcript, a secret or a path.** The script reads five whitelisted fields and the `result` text
  (`SAFE_FIELDS`, asserted as an exact set); it never reads `messages`, `content` or the prompt, and its fixture
  carries a transcript string that the test asserts is absent. It prints no path and the step has no `secrets.` or
  `GITHUB_TOKEN` in its body (asserted). `show_full_output: true` appears nowhere in the workflow file (asserted over
  the whole file, not only this step). The `result` text is capped at `MAX_ERROR_CHARS` (300).
- **Forge a workflow command.** The error text is joined onto one line with *every* line break collapsed, not only
  `\n`: the runner also ends a line at a bare `\r`. The first version replaced `\n` alone, and this review measured
  `"x\r::error::forged"` producing a line that begins `::error::`; fixed in the same branch, and the test now forges
  through `\n`, `\r`, `\r\n` and `\u2028` with the forged line **inside** the 300-character cap (beyond it, truncation
  alone would pass). Checked for teeth: reverting either fix makes
  `::test_the_report_names_the_failure_and_never_quotes_the_run` fail.
- **Turn a failed review green.** The step runs only under `failure() && steps.claude-review.outcome == 'failure'`,
  so it never runs on a green review; it carries no `continue-on-error`, and a later step that exits `0` cannot change
  the conclusion of a job whose earlier step already failed. `main` returns `0` on a missing or unreadable file
  (asserted), and the shell guard exits `0` when the base branch has no script, so the report itself adds no failure;
  an unexpected exception inside it would add a *second* red step to an already-red job, which is harmless and is in
  §6.

## 2. What was reviewed, and how deeply

Read in full, not in hunks:

| File | Why it is in the ranking |
|---|---|
| `src/custom_memory_engine/schema/033_erasure_markers.sql` | the schema change, its RLS and its three constraints |
| `src/custom_memory_engine/schema/rollback/033_erasure_markers.sql` | the rollback's completeness |
| `src/custom_memory_engine/deletion/markers.py` | `is_reconciled`, `record_reconciliation`, both marker writers, the flag |
| `src/custom_memory_engine/engine/deletion.py` | `_erase_marked` and both of its call sites; the epoch allocation and the receipt |
| `src/custom_memory_engine/deletion/journal.py` | format 2, merge identity, the replay's order and refusals |
| `src/custom_memory_engine/worker/loop.py` | `run_once`'s claim-then-check-then-release |
| `src/custom_memory_engine/host/service.py` | `start`, `_reconciled`, the readiness probe |
| `src/custom_memory_engine/semantic/slow_lane.py` | `release_one`'s fence, `_discard_stale`, `recover_stale`'s attempt accounting |
| `src/custom_memory_engine/schema/migrate.py` | `apply`/`reset` stamping, `TABLES`, the catalog fingerprint |
| `src/custom_memory_engine/security/rls.sql`, `security/provisioning.py` | the grant and RLS pair for the three new tables |
| `scripts/erasure_journal.py` | the operator's export and replay |
| `deploy/README.md` | the restore and upgrade procedure, read as a contract |
| `tests/integration/test_t46_restore_erasure_db.py` | the proof of the guarantee, read for what it would not catch |

Read for a specific claim, not in full: `schema/022_deletion_through_cognition.sql::delete_exclusive_turns` (the
`NOT EXISTS (job …)` and `memory_evidence` clauses behind §4's live-versus-replay paragraph) and
`security/rls.sql`'s `@create-role`/`@grants` sections (F1's third piece).

Traced outwards: `api/delete_handlers.py::replay`, `api/runtime.py::replayed` (the marker probe on the idempotent write
path), `semantic/slow_lane.py::claim_one`/`recover_stale`, `queue/worker.py::MAX_ATTEMPTS`,
`PLAN/V2/DECISIONS_V2.md` `V2-D125`'s Evidence and Cost paragraphs (every cited name resolves), and
`PLAN/V2/THREAT_MODEL.md` `T-46`.

## 3. Findings

`REVIEW.md` requires a reachability line on every finding, so each one has it. Severity is `REVIEW.md`'s (🔴 / 🟡);
the priority in brackets is the issue's own classification (`NOW` / `LATER`), which is what the tracker acts on.

| # | Severity | Finding | Reachability | Where it landed |
|---|---|---|---|---|
| F1 | 🔴 | An in-place upgrade 32 → 33 leaves the runtime role without grants on the three new tables; `host/service.py::start` then raises `InsufficientPrivilege` instead of `restore_not_reconciled`, every deletion fails at `deletion/markers.py`'s marker insert, the documented remedy (`provision_runtime_role`) cannot re-run on an existing role (`DuplicateObject`, uncaught by `scripts/provision_role.py`), and `deploy/README.md` names the wrong role for the read, so its remedy does not work either | reachable today: an operator who follows `deploy/README.md`'s upgrade paragraph on a running deployment creates exactly this state. Nothing upstream guards it — the migration has no grant step by design (`RL-071`), and the runtime role's missing privilege is checked nowhere before the first read | filed, `#268` (P2, `NOW`) |
| F2 | 🟡 | `scripts/erasure_journal.py replay --mark-reconciled` requires a `--journal` path it never opens, and `deploy/README.md`'s example supplies the off-host journal path in the one scenario where that file does not exist | reachable today: the command is the documented way out of F1's refuse-to-start state, and it is run by hand while the deployment is down. It succeeds, so nothing is erased wrongly; what is wrong is what the operator is told to type | filed, `#269` (P3, `NOW`) |
| F3 | 🟡 | A `turn` marker is recorded per turn for `scope='all'`, where the scope already names them and both branches recompute them on replay; the deletion transaction writes N rows and the journal — rewritten and fsynced in full on every export — grows by N triples per `all` request, permanently | reachable today: any `scope='all'` deletion on a user with a long history, with the journal timer enabled (`deploy/README.md:175`, the normal configuration). Cost only; nothing is served or erased wrongly | filed, `#270` (P3, `LATER`) |
| F4 | 🟡 | A failed review run published the shape of its failure and not the message, which is how `#264` came to record a comment in the action's `run:` block as the error | reachable today: every failed review run. It cannot make a review wrong, only unexplained — which it did, on this issue | fixed here: `.github/scripts/review_failure_report.py`, a step in `.github/workflows/claude-code-review.yml`, and three tests in `tests/test_claude_integration.py` |
| F5 | 🔴 | When the reconciliation check *raises* — F1's pre-33 grant state, or any error of that one query — `run_once` returns before `release_one`, so the claim's attempt is spent; three ten-minute leases later every queued extraction is `dead_letter` with no model ever called, which is the opposite of what the docstring promises and of what `test_the_worker_claims_nothing_until_the_journal_is_replayed_into_the_restored_database` asserts for the `False` answer | reachable today in F1's state (measured: three ticks, `attempts` 1 → 3, then `dead_letter`, zero extractor calls). Not a leak or a resurrection; it is queued learning lost without a retry | filed, `#304` (P2, `NOW`) |

Two 🔴 findings, and they are both about the *edge* of the mechanism rather than its core: the guarantee holds for a
correctly provisioned deployment (below, measured end to end), and what F1 and F5 break is the documented path to
becoming one. Neither erases, discloses or resurrects anything a caller should not reach: in both, the deployment is
already refusing to serve traffic, and F5's cost is lost extraction, not restored content. The one place a review could
inflate a finding — a runtime session inserting its own reconciliation row, which would defeat the gate — is written
down in §4 as *not* a finding, with the reason.

## 4. The questions this review was asked, settled

**`erased_turn` is a plaintext turn id, readable by the runtime role without a deletion key — does it leak?** No. It
holds `(turn_id, user_id)`, both of which `turn` already holds and which RLS scopes to the same user; measured on a
migrated schema, a second tenant in the runtime role reads `0` rows, and an insert naming another tenant's user is
refused (`InsufficientPrivilege`, the RLS `WITH CHECK`). It names no fact that the `turn` row does not, and the worker
can read it precisely because the key is not needed there (`V2-D125`).

**Can `HostService.start` failing closed deadlock a deployment with no journal to replay?** No. `start` closes the pool
and raises the static code `RestoreNotReconciled` (`restore_not_reconciled`), and `replay --mark-reconciled` is the
operator assertion that clears it — a code plus a documented one-line remedy is this repository's convention, not a gap.
What the record must say against itself: the message does not name the *remedy* (an operator has to grep
`restore_not_reconciled` into `deploy/README.md`), and in F1's state there is no `RestoreNotReconciled` to grep at all —
`start` raises `InsufficientPrivilege` instead. That second half is `#268`, not this paragraph.

**Can `release_one` launder an attempt budget?** No, and the question has a second half this review had to answer against
the code. On laundering: `claim_one` is the only incrementer (`attempts=attempts+1`), `release_one` is called from
exactly one place — `worker/loop.py::run_once`'s `not reconciled` branch — and its `GREATEST(attempts - 1, 0)` is
fenced by `status='running' AND attempts=%s` on the value the *claim* returned, so it can only undo the increment its
own claim made; a lease another worker has taken matches nothing and it returns `False`. `recover_stale` does not touch
`attempts` at all. Nothing can refund a budget it did not take. **But the inverse is the defect (F5, `#304`):** the
check is wrapped in no `try`, so when `is_reconciled` *raises* rather than answering `False`, `run_once` returns before
`release_one` runs and the claim's attempt is spent — measured, three ticks, `dead_letter`, zero extractor calls. The
existing test asserts the `False` answer only (`('queued', 0)` and no `job_` audit row), which is why the asymmetry was
invisible.

**Live versus replay for a `running` job.** Live, `_erase_marked` purges jobs `AND status <> 'running'` and leaves the
rest to the epoch guard; a turn whose job is `running` is then *kept* by `delete_exclusive_turns` (whose `NOT EXISTS
(job …)` clause refuses it) and flagged in `erased_turn`, so a re-extraction is dead-lettered with
`job_discarded_erased_turn`. Replay purges `running` jobs too. The asymmetry is deliberate and the doc says so. The path
that would break — erasing a turn while a live job still references it — is refused by that function, not by the
foreign key.

**Can a runtime-role session defeat the reconciliation gate itself?** It can, with one `INSERT` into
`restore_reconciliation` (`WITH CHECK (true)`, `SELECT, INSERT` granted) — measured. This is **not** a finding: the
actor that can do it is the actor the legitimate remedy runs as (`scripts/erasure_journal.py replay --mark-reconciled`
does exactly that statement, under that role), so it is not privilege escalation but the trust model. No caller path
reaches `record_reconciliation`: its only callers are the script and `schema/migrate.py`.

**The `(kind='write') = (result_id <> '')` check.** Enforced both ways in the catalog (measured: `CheckViolation`
23514 for a `turn` marker carrying a result and for a `write` marker without one). The composite foreign key
`(request_id, user_id) → deletion_request (id, user_id)` refuses both a marker for a request the database does not hold
and a marker filed under the wrong tenant (`ForeignKeyViolation` 23503) — the tenant check is the key, not only RLS, and
`deletion_request_id_user_id_key` is what makes it possible.

**The rollback's completeness.** Checked by running it, not by reading it: after `033` → `32`, `MigrationRunner.status`
reports `pending version 32` — not `catalog_drift` — which means the catalog fingerprint recorded when 32 was applied
matches the schema the rollback left. All three tables, the `deletion_request` unique constraint and the
`api_idempotency_result` index are gone; `apply` then returns to 33 cleanly. The one thing the *suite* does not assert
directly (`test_migrating_a_restored_database_does_not_mark_it_reconciled` rolls back and re-applies without checking
the fingerprint) is caught indirectly, because `apply` on a drifted catalog raises.

**`apply`/`reset` stamping.** `apply` stamps only when `status.state == "unapplied"` (a schema created from nothing)
and never for a `pending` upgrade; `reset` drops the schema and re-installs, which assigns a new relation OID, and
stamps the new one. Migration 33 deliberately stamps nothing, which is what makes a pre-33 dump restored-at-its-version
stay unreconciled.

## 5. Verified on a live database

PostgreSQL 16.15 + pgvector, in the clone database the integration fixture makes (`engine_database()`, so the schema is
whatever `MigrationRunner.apply` produces from nothing), with a fresh role per probe from `provision_runtime_role` and
every read and write run as that role under `SET LOCAL ROLE` and `app.user_id`:

| Claim | Result |
|---|---|
| a fresh install is reconciled; a copy whose row is gone is not | `True` / `False` |
| RLS is on and forced, with the stated policies | `erased_turn` T/T/1, `erasure_marker` T/T/1, `restore_reconciliation` T/T/2 |
| a second tenant in the runtime role sees nothing | `0` markers, `0` flags; an insert for another user → `InsufficientPrivilege` |
| grants match the least-privilege matrix | the three tables: `INSERT,SELECT`; `erasure_marker`/`erased_turn` append-only |
| the CHECK and the composite FK | `CheckViolation` 23514 ×2, `ForeignKeyViolation` 23503 ×2 |
| the rollback restores version 32 | `pending version 32`, tables/constraint/index gone, re-apply OK |
| the pre-33 grant set breaks the upgrade | `InsufficientPrivilege` on `restore_reconciliation` and on `erasure_marker` (F1) |
| the same state spends a claimed job's attempts (F5) | `run_once` raises `InsufficientPrivilege` with the job `running` and `attempts` 1 → 2 → 3; `recover_stale` returns it to `queued` twice and then to `dead_letter`; the extractor is called `0` times |

Every row was re-measured by this record on `cme_compatibility_gw13`, in a clone database the integration fixture makes,
not only asserted from reading. Test suites run: `tests/integration/test_t46_restore_erasure_db.py` (7 passed),
`tests/test_claude_integration.py`, `tests/test_review_gate.py`, `tests/test_ci_workflow.py` (136 passed), `ruff check`,
`ruff format --check`, `pyright` (0 errors, with `--extra baselines` as CI's `static` job does), `actionlint` over
`.github/` (actionlint 1.7.12, run as `uvx --from actionlint-py actionlint .github/workflows/*.yml`: 0 findings),
`scripts/check_plan_refs.py` (0 findings) and `scripts/check_evidence.py` over this file (37 references, 0 unresolved).

The probes live in no committed file — they were run from `/tmp` against the same fixtures the integration tests use
(`tests/integration/schema.py::engine_database`), which is why they are quoted here rather than cited as a path.

## 6. Not verified

- **The review job's own fix is not proven on a failed runner.** The step and the script are pinned by tests that read
  text and run the parser; nothing here has executed them on a failed runner. They are exercised on the next failure,
  which is also the next time this record's §1 can be checked. Two specifics of that run are unproven: that the action's
  `claude-execution-output.json` carries the `result` object (and its `result` text) on a *failed* run — the green runs'
  output does — and that GitHub masks a secret if one ever appeared inside the run's own message text.
- **The *identity* of the 2026-11-26 cause, as opposed to its class.** The shape (one turn, no cost, ~380 ms) says the
  first call to the model failed; it cannot say why, whether an operator re-issued `CLAUDE_CODE_OAUTH_TOKEN`, or whether
  the condition cleared on the provider's side. Nothing in the repository changed between the red runs and the green
  ones, so git cannot settle it either; the next failure's report step is what would.
- **The rehearse-restore path.** `scripts/rehearse_restore.py` and `tests/integration/test_backup_restore_db.py` run
  `pg_dump`/`pg_restore` and were read but not executed here; CI's `verify` job is where they run. That is the one place
  the two resurrection paths are exercised against a real dump rather than a simulated restore.
- **`dual-profile`** (advisory, one migrated database, both profiles) was not run: it needs the prepared model assets,
  and nothing in this change touches either profile.
- **A `scope='all'` deletion, end to end.** No test in the tree deletes with `scope='all'` and inspects the markers,
  which is part of why `#270` is a finding and not a measurement.

## 7. Why this review can be trusted

Every claim above names either a file and symbol that was read whole, or a command whose output is quoted (§1's log
lines and job ids are from `gh api .../actions/jobs/<id>/logs`, not from a summary of them). The places where this
record disagrees with a document — `deploy/README.md`'s login-role sentence and its `version == 32` check (`#268`),
`V2-D125`'s "bounded by the closure it already traversed" (`#270`), `run_once`'s "put back without spending an attempt"
(`#304`) — are stated with the code that contradicts it. The one place a finding could have been inflated — a runtime
session inserting its own reconciliation row — is written down as *not* a finding, with the reason: the legitimate
remedy performs the same statement under the same role. And the two places a review can silently weaken a guarantee were
checked by hand: the tests' teeth (both resurrection tests carry an in-test **control** that removes the marker and
reproduces the resurrection), and the new report's, by breaking each of its two defences in turn and watching the test
fail — `\n`-only replacement fails on `\r`, and the forgery test placed beyond the cap fails on truncation alone.

The draft of this record was wrong in three places, and they are left visible rather than quietly edited out: it called
the review job's failure permanent and operator-fixable when it had already cleared (§1), it attributed the
"not self-describing error" to `#269`, which is about a required argument (§4), and it stopped at `release_one`'s
fence without following the exception path (§4, F5). Each is corrected in place, and the corrections are the reason
the record is worth reading as a review rather than as an approval.
