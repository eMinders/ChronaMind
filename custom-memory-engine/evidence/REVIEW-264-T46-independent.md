# Independent review of migration 33 and the restore-reconciliation gate (`T-46`, `V2-D125`)

**Issue:** `#264` · **Reviewed tree:** `main` at `862e7d5`, which carries the merge `321f108` and the two tracking
commits after it · **Changed by this record:** nothing under review; this is the review, and the one tooling change it
needed lives in `.github/`

**Why here.** `evidence/` is where this repository keeps written review and verification records — `VERIFICATION.md`,
`POSTMERGE-assurance.md`, `RL-0NN-verification.md` — and `reports/` is the graduation-project research record (its
`README.md` scopes it to H1–H4). A review of merged code is the `POSTMERGE-assurance.md` shape: a base commit, a table of
claim → direct verification, and what the record could not verify.

**Why it exists at all.** `#260` merged with a caveat: its final head (`7c4ad14`) was never reviewed, because every
attempt of the review job failed with no message. The three things this record settles are therefore (1) what that
failure actually was, (2) a fresh-eyes review of the merged whole, and (3) where each thing it found was filed.

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

— echoed by the runner's `##[group]Run` header before anything executes. It is present in the **green** run
`45845609418` too (that job log, line 422 of the step, `is_error: false`, nine turns, `$0.138`), which is what rules it
out as the cause. Both jobs ran the same action commit (`4d75e5f`), the same CLI (`Claude Code v2.1.323`), the same
runner image and the same `SDK options` block, fifteen minutes apart.

**What actually failed.** The run died before its first answer:

```json
{"type": "result", "subtype": "success", "is_error": true, "duration_ms": 377,
 "num_turns": 1, "total_cost_usd": 0, "permission_denials_count": 0, "modelUsage": {}}
```

`num_turns: 1`, `total_cost_usd: 0` and an empty `modelUsage` mean no tokens were billed: the CLI produced an error
result instead of a completion, and the action turns `is_error: true` into a failed step. The message itself was never
printed — `show_full_output` is false — so the repository cannot name the cause from its own logs, only its class: the
first call to the model failed for a reason on the credential/model side (`secrets.CLAUDE_CODE_OAUTH_TOKEN` invalid,
revoked or over its limit, or a model id the account cannot use). Content is excluded: run `45852260361` failed
identically on a **docs-only** pull request (`#265`, two markdown files), and the last green run reviewed a tree that
differs from `7c4ad14` by three lines of prose.

**Conclusion, stated plainly.** This failure is not fixable in this repository. It began between 15:06Z (green) and
15:21Z (red) on 2026-11-26, affected every run after it, and the fix is an operator action: re-issue or replace the
`CLAUDE_CODE_OAUTH_TOKEN` secret. The substitute process — which is what this record is — is a hand-run review against
`.claude/skills/review-pr/SKILL.md`, recorded on the issue.

**The one thing that was fixable here.** The reason this issue could be filed with a wrong root cause is that a failed
run said nothing. `.github/scripts/review_failure_report.py` now prints the run's own `result` fields on failure — the
error text among them, capped at 300 characters, and nothing from `messages`, `content` or the prompt — and
`.github/workflows/claude-code-review.yml` runs it only when the review step itself failed. Pinned by
`tests/test_claude_integration.py::test_a_failed_review_reports_why_with_the_error_text_and_not_the_transcript`,
`::test_the_report_names_the_failure_and_never_quotes_the_run` and
`::test_the_report_never_claims_a_cause_it_cannot_see`.

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

Traced outwards: `api/delete_handlers.py::replay`, `api/runtime.py::replayed` (the marker probe on the idempotent write
path), `semantic/slow_lane.py::claim_one`, `PLAN/V2/DECISIONS_V2.md` `V2-D125`'s Evidence and Cost paragraphs (every
cited name resolves), and `PLAN/V2/THREAT_MODEL.md` `T-46`.

## 3. Findings

| # | Severity | Finding | Where it landed |
|---|---|---|---|
| F1 | 🟡 | An in-place upgrade 32 → 33 leaves the runtime role without grants on the three new tables; `host/service.py::start` then raises `InsufficientPrivilege` instead of `restore_not_reconciled`, every deletion fails at `deletion/markers.py`'s marker insert, the documented remedy (`provision_runtime_role`) cannot re-run on an existing role (`DuplicateObject`, uncaught by `scripts/provision_role.py`), and `deploy/README.md` names the wrong role for the read, so its remedy does not work either | filed, `#268` (P2) |
| F2 | 🟡 | `scripts/erasure_journal.py replay --mark-reconciled` requires a `--journal` path it never opens, and `deploy/README.md`'s example supplies the off-host journal path in the one scenario where that file does not exist | filed, `#269` (P3) |
| F3 | 🟡 | A `turn` marker is recorded per turn for `scope='all'`, where the scope already names them and both branches recompute them on replay; the deletion transaction writes N rows and the journal — rewritten and fsynced in full on every export — grows by N triples per `all` request, permanently | filed, `#270` (P3) |
| F4 | 🟡 | A failed review run published the shape of its failure and not the message, which is how `#264` came to record a comment in the action's `run:` block as the error | fixed here: `.github/scripts/review_failure_report.py`, a step in `.github/workflows/claude-code-review.yml`, and three tests in `tests/test_claude_integration.py` |

No 🔴 finding. That is a claim, so here is what it rests on: the guarantee itself holds end to end, on a real database,
through the deletion path (below); the schema's constraints do what the migration says; the rollback restores version 32
exactly; and the reconciliation identity distinguishes a fresh install from a restored copy. What F1 shows is that the
*procedure around* the mechanism had a hole, not that the mechanism is wrong.

## 4. The questions this review was asked, settled

**`erased_turn` is a plaintext turn id, readable by the runtime role without a deletion key — does it leak?** No. It
holds `(turn_id, user_id)`, both of which `turn` already holds and which RLS scopes to the same user; measured on a
migrated schema, a second tenant in the runtime role reads `0` rows, and an insert naming another tenant's user is
refused (`InsufficientPrivilege`, the RLS `WITH CHECK`). It names no fact that the `turn` row does not, and the worker
can read it precisely because the key is not needed there (`V2-D125`).

**Can `HostService.start` failing closed deadlock a deployment with no journal to replay?** No, and the escape hatch is
documented — but the *error* is not self-describing, which is F1's third piece and `#269`. `start` closes the pool and
raises `RestoreNotReconciled`; `replay --mark-reconciled` is the operator assertion. The remedy is discoverable by
grepping `restore_not_reconciled` into `deploy/README.md`, not from the message.

**Can `release_one` launder an attempt budget?** No. `claim_one` is the only incrementer (`attempts=attempts+1`), and
`release_one` is called from exactly one place — `worker/loop.py::run_once`'s `not reconciled` branch — with the fence
`status='running' AND attempts=%s` on the value the claim returned, so a lease another worker has taken matches nothing
and it returns `False`. `recover_stale` does not touch `attempts` at all; it dead-letters at `MAX_ATTEMPTS` or returns
the job to `queued`. So nothing in the path can spend a job's budget, and nothing can refund one it did not take.
`test_the_worker_claims_nothing_until_the_journal_is_replayed_into_the_restored_database` asserts the durable half of
that: `('queued', 0)` and no `job_` audit row after an `unreconciled` tick.

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

PostgreSQL 16.15 + pgvector, a throwaway `cme_probe264_*` schema created by `MigrationRunner.apply`, a role by
`provision_runtime_role`, and the probes run as that role:

| Claim | Result |
|---|---|
| a fresh install is reconciled; a copy whose row is gone is not | `True` / `False` |
| RLS is on and forced, with the stated policies | `erased_turn` T/T/1, `erasure_marker` T/T/1, `restore_reconciliation` T/T/2 |
| a second tenant in the runtime role sees nothing | `0` markers, `0` flags; an insert for another user → `InsufficientPrivilege` |
| grants match the least-privilege matrix | the three tables: `INSERT,SELECT`; `erasure_marker`/`erased_turn` append-only |
| the CHECK and the composite FK | `CheckViolation` 23514 ×2, `ForeignKeyViolation` 23503 ×2 |
| the rollback restores version 32 | `pending version 32`, tables/constraint/index gone, re-apply OK |
| the pre-33 grant set breaks the upgrade | `InsufficientPrivilege` on `restore_reconciliation` and on `erasure_marker` (F1) |

Test suites run: `tests/integration/test_t46_restore_erasure_db.py` (7 passed), `tests/test_claude_integration.py`,
`tests/test_review_gate.py`, `tests/test_ci_workflow.py` (136 passed), `ruff check`, `ruff format --check`,
`pyright` (0 errors, with `--extra baselines` as CI's `static` job does), `actionlint` 1.7.12 over `.github/`.

## 6. Not verified

- **The review job's own fix is not proven by CI.** The step and the script are pinned by tests that read text and run
  the parser; nothing here has executed them on a failed runner. They are exercised on the next failure, which is also
  the next time this record's §1 can be checked.
- **Whether `CLAUDE_CODE_OAUTH_TOKEN` is the credential at fault.** The shape of the failure (one turn, no cost) says
  the first call to the model failed; it cannot say why. Only the operator's account settings can, and only the new
  report step will print it.
- **The rehearse-restore path.** `scripts/rehearse_restore.py` and `tests/integration/test_backup_restore_db.py` run
  `pg_dump`/`pg_restore` and were read but not executed here; CI's `verify` job is where they run. That is the one place
  the two resurrection paths are exercised against a real dump rather than a simulated restore.
- **`dual-profile`** (advisory, one migrated database, both profiles) was not run: it needs the prepared model assets,
  and nothing in this change touches either profile.
- **A `scope='all'` deletion, end to end.** No test in the tree deletes with `scope='all'` and inspects the markers,
  which is part of why `#270` is a finding and not a measurement.

## 7. Why this review can be trusted

Every claim above names either a file and symbol that was read whole, or a command whose output is quoted. The two
places where the reviewer disagrees with a document (`#268`'s wrong role, `#270`'s cost) are stated with the code that
contradicts it, not with an impression. The one place a finding could have been inflated — a runtime session inserting
its own reconciliation row — is written down as *not* a finding, with the reason: the legitimate remedy performs the same
statement under the same role. And the one place a review can silently weaken a guarantee, the tests' own teeth, was
checked by reading what the tests would catch if the code reverted: both resurrection tests carry an in-test **control**
that removes the marker and reproduces the resurrection, which is the strongest form available here.
