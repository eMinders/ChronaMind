# Full schema (`RL-040`)

Canonical PostgreSQL DDL for the twelve tables in `TECHNICAL_SPECIFICATION.md` §3.1 and `Graduation_Project_Proposal.md` §8.

| File | Role |
|---|---|
| `001_full_schema.sql` | Tables, invariants 1–4/7/9 constraints, `clock_now()`, `guard_update()`, RLS scaffolding |
| `bitemporal_on_full.sql` | The 43 reduced-schema assertions from `validation/bitemporal_checks.sql`, replayed against this `memory_version` table |
| `init_extensions.sql` | `vector` + `btree_gist` bootstrap for disposable databases |

[`migrate.py`](migrate.py) implements the `RL-041` migration runner described below. `RL-042` owns transactional supersession and `archive_version`. `RL-043` owns retrieval functions as engine APIs. `bitemporal_on_full.sql` still defines the design-check query helpers locally so the 43 assertions can run before those tasks.

## Apply, inspect and clean reset (`RL-041`)

From `custom-memory-engine/`, export **`RL_DATABASE_URL`** for a dedicated research
database, then run:

```text
uv run python -m custom_memory_engine.schema.migrate status
uv run python -m custom_memory_engine.schema.migrate apply
uv run python -m custom_memory_engine.schema.migrate reset --confirm-schema cme_engine
```

The database URL must explicitly identify its host, user and database; system
databases are rejected. The runner never loads a dotenv file or falls back to the
product's `DATABASE_URL`. For an already configured disposable test database, an
explicit PowerShell assignment is `$env:RL_DATABASE_URL = $env:CME_TEST_DATABASE_URL`.
Keep credentials in the environment, outside command arguments and reports.

The default target is **`cme_engine`**. `--schema cme_other_name` selects another
lowercase `cme_` namespace; reset requires that exact value in `--confirm-schema`.
Only schemas created and marked by this runner, owned by the current role, are
managed. Existing unmarked schemas are never adopted or erased. The twelve
canonical tables stay unchanged; `_cme_schema_migrations` is a private thirteenth
table recording version, filename, canonical-LF source SHA-256, live-catalog digest
and application time. Version **1 / `001_full_schema.sql`** is currently registered.
Unknown versions and edited historical sources are refused, rather than reapplied.

`status` reads without creating schema objects and reports `unapplied`, `applied`,
`unmanaged` or `drifted`, plus the recorded version/checksum and a static reason.
The live digest checks columns/defaults, relations/owners, constraints and their
owning tables, indexes, functions, triggers (including internal foreign-key firing
state), RLS/policies, types and extension versions; row data and
storage statistics are excluded. `apply` returns `unchanged` on a matching install
and preserves both rows and table identities. Constructors perform no I/O.

All operations use an async connection and a bounded transaction-level advisory
lock. Apply and reset commit atomically. The provisioning role needs schema and
extension creation privileges; runtime role grants remain `RL-071`. The runner
installs/verifies `vector` and `btree_gist` in **`public` before** selecting the
managed creation namespace, so reset cannot erase those shared extensions.
The supported integration stack is PostgreSQL 16.15 / vector 0.8.7 / btree_gist 1.7;
the runner records actual extension versions, rather than silently upgrading them.

Clean reset removes data in the known managed tables and recreates migration 1.
It can repair known catalog drift after explicit confirmation. Invalid/missing
history, unknown versions and source-checksum drift are refused. The runner drops
known tables together with **`RESTRICT`**, then the three known functions and the
schema, also with `RESTRICT`. External dependencies or unknown remaining objects
make reset fail and roll back; no cross-schema `CASCADE` is used. A failed reapply
also restores the original rows, table identities and history. Normal operation
must stop before an operator intentionally resets its namespace.

The async Python API is `MigrationRunner(MigrationConfig(...))`, with `status()`,
`apply()` and `reset(confirm_schema=...)`. Records are immutable; the public runner
revalidates copied configuration. CLI output uses JSON, with diagnostics on stderr;
errors contain static categories/SQLSTATE, never provider content or connection
credentials. Windows CLI execution uses a selector event loop for psycopg.

Pre-merge installations created before the constraint-enforcement fingerprint
correction report `catalog_drift` with the revised runner. `apply` does not rewrite
their recorded fingerprint. A confirmed reset recreates disposable fixtures;
databases whose rows must be retained require an operator-reviewed migration
instead of a reset.

`tests/integration/test_migrations.py` exercises real provisioning, repeated
apply/reset, failure rollback, concurrent apply, ownership/drift, external
dependency preservation and first-run extension bootstrap in fresh databases.
Its clean-reset fixture runs all **43** assertions from `bitemporal_on_full.sql`
and checks the original full table identity and full-only columns remain present.
That fixture script contains explicit transactions, test rows, local helper
functions and a temporary provenance-trigger disable; it is **not** a production
verification command. The migration CLI never executes it on runtime data.

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

`tests/integration/test_full_schema.py` applies `001_full_schema.sql` into a throwaway schema, checks the twelve tables, the named constraints, `FORCE ROW LEVEL SECURITY`, the deferred provenance trigger, `guard_update()`, and both `JOB` unique constraints, then replays `bitemporal_on_full.sql` and requires exactly 43 `PASS` results. Run it against the disposable database from `compose.yaml`:

```bash
CME_TEST_DATABASE_URL=postgresql://cme_test:cme_disposable_local_only@127.0.0.1:5433/cme_compatibility \
  uv run pytest -q tests/integration/test_full_schema.py
```

The test skips when `CME_TEST_DATABASE_URL` is absent. CI runs it against the pinned `pgvector/pgvector:0.8.7-pg16-bookworm` image (`D-25`).
