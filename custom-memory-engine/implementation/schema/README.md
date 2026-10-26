# Full schema (`RL-040`)

Canonical PostgreSQL DDL for the twelve tables in `TECHNICAL_SPECIFICATION.md` §3.1 and `Graduation_Project_Proposal.md` §8.

| File | Role |
|---|---|
| `001_full_schema.sql` | Tables, invariants 1–4/7/9 constraints, `clock_now()`, `guard_update()`, RLS scaffolding |
| `bitemporal_on_full.sql` | The 43 reduced-schema assertions from `validation/bitemporal_checks.sql`, replayed against this `memory_version` table |
| `init_extensions.sql` | `vector` + `btree_gist` bootstrap for disposable databases |

`RL-041` owns migration runners. `RL-042` owns transactional supersession and `archive_version`. `RL-043` owns retrieval functions as engine APIs. `bitemporal_on_full.sql` still defines the design-check query helpers locally so the 43 assertions can run before those tasks.

## Invariant map

| Invariant | Enforcement site in this DDL | Notes |
|---|---|---|
| **Invariant 1** | Constraint trigger `trg_memory_version_provenance` on `memory_version`, function `enforce_provenance()`, `DEFERRABLE INITIALLY DEFERRED` | Commit of `active`/`archived` requires ≥1 `memory_evidence` row or ≥1 outgoing `memory_derivation` row. `bitemporal_on_full.sql` disables it because that harness inserts believed rows without evidence, and re-enables it as its final statement, so a replay never leaves a persistent database with invariant 1 off. |
| **Invariant 2** | `CHECK` constraints `valid_wellformed`, `recorded_iff_believed`, `recorded_wellformed` on `memory_version` | Same predicates as the reduced schema. |
| **Invariant 3** | `clock_now()` reads `app.clock` then `transaction_timestamp()`; `BEFORE UPDATE` trigger `trg_memory_version_guard_update` / `guard_update()` | One instant per transaction, so `recorded_range` bounds written by several statements share `c` (R1). Post-insert updates allowed only for `status`, `archival_branch` (only together with `status = 'archived'`), staging-to-active `recorded_range = [clock_now(), ∞)`, and closing `upper(recorded_range)` to `clock_now()`. Replay monotonicity remains a harness/`RL-044` concern. |
| **Invariant 4** | `EXCLUDE USING gist` constraint `no_bitemporal_overlap` (`btree_gist`) | Partial: `single_valued AND status IN ('active','archived')`. Per-user advisory locks stay in `RL-042`. |
| **Invariant 5** | Not a row constraint. `guard_update()` blocks in-place content edits; supersession procedures are `RL-042` | Worked-example close-then-insert is exercised by `bitemporal_on_full.sql`. |
| **Invariant 6** | `recorded_iff_believed` plus query helpers in `bitemporal_on_full.sql` (`q_current`, `q_historical`, `q_change`, `q_audit`) | Engine-only read path and role lock-down are `RL-043` / `RL-071`. |
| **Invariant 7** | `user_id` (or `app_user.id`) on every user-data table; `ENABLE` and `FORCE ROW LEVEL SECURITY`; policy `*_isolation` using `current_setting('app.user_id', true)` for both `USING` and `WITH CHECK` | `predicate_schema` is shared catalog, not user data. Runtime grants are `RL-071`. |
| **Invariant 8** | Not a row constraint. `ON DELETE CASCADE` from `memory_version` to evidence/derivation/embedding/conflict; `audit_event.details` `CHECK` `audit_details_content_free` | Seven-channel deletion procedure is `RL-070`. |
| **Invariant 9** | `job.idempotency_key` `UNIQUE`; `CONSTRAINT job_extraction_idempotent UNIQUE (user_id, turn_id, extractor_version, candidate_fingerprint)` | Extra `job` columns exist only to hold that extraction identity. Standard PostgreSQL uniqueness treats `NULL` extraction identities as distinct, so jobs of other kinds are not deduplicated by this constraint. |
| **Invariant 10** | Not a single-row constraint | `archive_version` / V4 `(S) ∨ (C)` is `RL-042` (`D-27`). `archival_branch` is stored here so that procedure can record the branch. |

## Compatibility with `validation/bitemporal_checks.sql`

`memory_version` keeps the reduced-schema column order for the first ten columns and supplies defaults for the extra specification columns (`layer`, `valid_from_basis`, `assertion_mode`, `extractor_version`, `reinforcement_count`). The 43 assertions therefore target the full table rather than a second reduced copy.

## Why this file rather than the spec-named one

`TECHNICAL_SPECIFICATION.md` names `validation/bitemporal_checks.sql` as the artifact for these invariant checks. That file builds its own reduced `memory_version` and stays the source of the 43 assertions. `bitemporal_on_full.sql` replays the same assertions against the twelve-table schema, so the reduced copy and the real table cannot drift apart. `test_full_schema.py` requires exactly 43 `PASS` results from the replay and checks that the replay leaves the provenance trigger enabled.

## Deliberate additions beyond §3.1

§3.1 lists key columns, not a closed column set. These additions are load-bearing and worth naming:

| Addition | Why |
|---|---|
| `deletion_request.status` CHECK (`pending`, `running`, `completed`, `failed`) | §7.7 tracks execution state, and an unconstrained status would accept typos. |
| `memory_embedding.embedding vector(384)` | Pins the dimension of the frozen embedding model. `D-25` owns that model choice. |
| `session_bounds`, `derivation_not_self`, `UNIQUE (session_id, sequence_no)` | Reject a negative session duration, a self-referential derivation, and duplicate turn ordering. |
| `job.extractor_version`, `job.candidate_fingerprint` | Hold the invariant 9 extraction identity. |

## Verification

`implementation/tests/test_full_schema.py` applies `001_full_schema.sql` into a throwaway schema, checks the twelve tables, the named constraints, `FORCE ROW LEVEL SECURITY`, the deferred provenance trigger, `guard_update()`, and both `JOB` unique constraints, then replays `bitemporal_on_full.sql` and requires exactly 43 `PASS` results. Run it against the disposable database from `compose.yaml`:

```bash
CME_TEST_DATABASE_URL=postgresql://cme_test:cme_disposable_local_only@127.0.0.1:5433/cme_compatibility \
  uv run pytest -q implementation/tests/test_full_schema.py
```

The test skips when `CME_TEST_DATABASE_URL` is absent. CI runs it against the pinned `pgvector/pgvector:0.8.7-pg16-bookworm` image (`D-25`).
