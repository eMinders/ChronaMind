# Full schema (`RL-040`)

Canonical PostgreSQL DDL for the twelve tables in `TECHNICAL_SPECIFICATION.md` §3.1 and `Graduation_Project_Proposal.md` §8.

| File | Role |
|---|---|
| `001_full_schema.sql` | Migration 1: tables, invariants 1–4/7/9/10 constraints, `clock_now()`, `guard_update()`, RLS policies |
| `002_scope_grants.sql` | Migration 2: `scope_grant` (read-membership ACL, `V2-D36`) |
| `003_association_cap.sql` | Migration 3: `enforce_association_cap()` (`V2-D13`) |
| `004_tenant_integrity.sql` | Migration 4 (`V2-D54`): every intra-tenant foreign key composite on `(id, user_id)`; every engine function pinned to `search_path = <schema>, pg_catalog, pg_temp`; grants are never deleted |
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
and application time. Migrations **1–9** are registered in `migrate.MIGRATIONS` (append-only); integration suites build their
schema from the same list (`tests/integration/schema.py`), so every migration is exercised by every suite.
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
known tables together with **`RESTRICT`**, then the known functions (`migrate.FUNCTIONS`) and the
schema, also with `RESTRICT`. External dependencies or unknown remaining objects
make reset fail and roll back; no cross-schema `CASCADE` is used. A failed reapply
also restores the original rows, table identities and history. Normal operation
must stop before an operator intentionally resets its namespace.

## Migration 5: independent axes (`005_axes.sql`, `E-010`, M1 + M7)

Adds `kind`, `lifecycle` and `retention` to `memory_version` (appended as columns 21-23, `NOT NULL`, exact `CHECK`
enums), backfills every existing row, and keeps them consistent with the v1 `layer`/`status` columns during the
dual-write window. The mapping is the approved one (`V2-D38`; `model/axes.py::V1_BACKFILL_SQL`):
`episodic/semantic/opinion/persona -> episode/claim/claim/abstraction`; an `active` row is `observed` (episode),
`inferred` (persona, or a claim with a derivation row) or `asserted`; `conflict_pending -> contested`;
`archived`, `rejected` and `candidate -> retired`; retention is `durable` for every v1 row. `archived` is a
**lifecycle** (`retired`), never a retention value (M7). `layer` stays stored (`V2-D49` D6).

* **One mapping, one place.** `sync_memory_version_axes` (a `BEFORE INSERT OR UPDATE` trigger) holds the mapping.
  The backfill is a no-op `UPDATE` that fires it, so the backfill and every later write use the same code, and no v1
  write path can bypass it (none had to change). Code that supplies the axes itself is validated against
  `layer`/`status`, not overwritten: a contradiction raises `axes_kind_inconsistent_with_layer` or
  `axes_lifecycle_inconsistent_with_status`.
* **Derivation.** v1 inserts a version before its derivation rows, so a derived claim is first `asserted` and
  `mark_derived_version_inferred` (an `AFTER INSERT` trigger on `memory_derivation`) makes it `inferred`.
* **`kind` is immutable.** `guard_update` is replaced with `kind` in its immutable tuple; `kind` is also a function
  of the immutable `layer`, so the change is refused twice.
* **Not here.** Lifecycle transition legality (`E-016`), retention floors (`E-020`), scope (`E-011`), dropping `layer`.
  The window ends when the last v1 reader of `layer`/`status` is removed; that is an owner decision.
* **Rollback.** `rollback/005_axes.sql` (run it in one transaction; the runner never executes it) restores the
  pre-M1 schema: the catalog fingerprint afterwards equals the one recorded for version 4. After a re-apply every
  catalog property is identical except that the three re-added columns have attribute numbers 23-25 instead of
  20-22, because PostgreSQL never reuses a dropped attribute number.
* **Applying to existing data.** The backfill rewrites every row and re-fires the provenance check, which the
  migration flushes (`SET CONSTRAINTS ... IMMEDIATE`) before it alters the table. Run it with the queue drained
  (`MIGRATION_RISK.md` M1); at research scale it is one short transaction.

Tested by `tests/integration/test_migration_m1.py`.

## Migration 6: facet tag and retention floors (`006_facet_floors.sql`, `E-020`, M8)

Adds the nullable `um_facet` tag (exactly `preference | goal | constraint | context | decision_pattern |
meta_preference`; `inferred_preference` is rejected because inference is a lifecycle, `USER_MODEL.md` section 2.1) and
the **retention floor**: `kind IN ('claim','abstraction')` implies `retention = 'durable'`, so only episodes and
procedures may be `working` or `session` (`V2-D29`). Both are table `CHECK` constraints, so they hold on `INSERT` and
`UPDATE` for every writer. The index `(user_id, um_facet) WHERE um_facet IS NOT NULL` serves facet-filtered queries per
user; `E-011` recreates it on its scope column. Requires migration 5. Every existing row keeps `um_facet` `NULL` and
already satisfies the floor (v1 has no `working`/`session` value). Rollback: `rollback/006_facet_floors.sql` (drops
the tags). Purge and expiry behaviour is `E-026`'s. Tested by `tests/integration/test_retention_floors.py`.

## Migration 7: the lifecycle guard (`007_lifecycle_guard.sql`, `E-016`)

The §2.3 transition table is closed in the database, for every writer (direct SQL under a restricted role included):

* **Legality by kind** (`V2-D48` C-P1): `CHECK memory_version_lifecycle_legal_for_kind` mirrors `model.axes.LEGAL_LIFECYCLE`
  (an abstraction is never `asserted`, an episode never adjudicated, a procedure never `observed`).
* **Transitions** (`enforce_lifecycle_transition`, `BEFORE INSERT OR UPDATE`): only `contested -> asserted | retired`,
  `asserted | observed | inferred -> retired` and `inferred -> contested` succeed. `retired` is terminal
  (`lifecycle_retired_is_terminal`) and `inferred -> asserted` has its own code
  (`lifecycle_inferred_to_asserted_unrepresentable`); everything else is `lifecycle_transition_not_permitted`.
  Re-asserting retired content creates a new row.
* **Records at commit** (extends `enforce_provenance`, so it shares `trg_memory_version_provenance`): an active
  `observed`/`asserted` row needs an evidence row; an active `inferred` row needs a derivation row (`I1′`, derivation half).
* **Not yet checkable** (named, not faked): the `I1′` method reference and the adjudication, verification and
  retirement-reason records have no table until `E-014`, which extends `enforce_provenance` when it adds them.
* **Dual-write allowances** (end with the window): staged `candidate`/`rejected` rows are born `retired` and a
  `candidate` may be promoted; a derived claim becomes `inferred` when its derivation row arrives; accepting a held
  derived/persona candidate activates it as `inferred`; v1 `persona` rows are exempt from the derivation record.
* **Decision (a)** (does `I10` apply to explicit withdrawal?) is unmade. As the issue instructs, `(S)` or `(C)` is
  required for system-initiated retirement only, which is migration 1's archival trigger on `status = 'archived'`.

Rollback: `rollback/007_lifecycle_guard.sql`. Tested by `tests/integration/test_lifecycle_transitions.py`.

## Migration 8: generalised evidence source (`008_source.sql`, `E-013`, M4)

`memory_evidence` gains `source_type`, `source_ref`, `span`, `content_hash`, so evidence is a *source reference*, not
only a chat turn. The vocabulary is closed at the two types `V2-D41` declares, `turn` and `external` (an `artifact` is
never evidence); `content_hash` is SHA-256 over the NFC-normalised, LF-newline UTF-8 text
(`grounding.sources.canonical_hash`; the SQL backfill and the Python function are tested to agree).

* **Turn evidence is byte-identical.** The old columns are never written by the migration; the backfill fills only
  the new ones (`('turn', turn_id, evidence_span, sha256(turn content))`) and a `CHECK` keeps them equal for every turn.
  `turn_id` and its foreign key stay (nullable, `NULL` for external sources), so a dangling turn is still refused.
  The primary key moves to `(version_id, source_type, source_ref, span)`, which is the old key for turns.
* **Dual-write.** v1 writers still insert `(version_id, turn_id, user_id, evidence_span, evidence_role)`;
  `sync_memory_evidence_source` derives the source columns, so the engine's turn inserts are exactly v1's.
* **External sources** need a hash and a locator and no engine row. They are **outside the deletion boundary**
  (`D-ENG-04`): deleting an episode removes its row and derivations, and `DeletionReceipt.external_exclusions` lists the
  external sources it cited by `(source_type, content_hash)`, exactly as WAL and backups are excluded. The audit event stores a count only (hashes are never audited, `RL-072`). `engine/sources.py::fetch_sources` reads a version's sources back with provenance.
* **Invariant 1 / `I2`** are unchanged in form: the deferred provenance trigger counts any evidence row.
* **Rollback** (`rollback/008_source.sql`) refuses with `m4_rollback_refused_non_turn_sources_exist` while any
  non-turn source exists; with turn sources only it restores version 7 exactly and the evidence data is untouched.
* **Open for the owner** (issue question (b)): the type vocabulary is closed in the schema, so adding a type is a
  migration, not a profile setting.

Tested by `tests/integration/test_migration_m4.py`.

## Migration 9: the three clocks (`009_clocks.sql`, `E-012`, M3)

`valid_range` becomes `event_range` **in place** (`V2-D49` D9: the exclusion constraint `no_bitemporal_overlap`, the `CHECK`
`valid_wellformed` and `memory_version_temporal_idx` follow the column, so the constraint is never dropped and Invariant 4 has
no unguarded window; this amends steps 1-3 of issue #63). The key stays `user_id` (D10). Three functions name the column
(`guard_update`, `enforce_archival_invariant`, `guard_derivation_coverage`); they are rewritten from their own catalog
definitions, which keeps their pinned `search_path`. `bitemporal_on_full.sql` is adapted (and steps the new watermark aside
for its deliberate time travel, as it already does for provenance); its 43 assertions run on the whole migration chain.

* **`observed_at`** (engine clock at ingest, fixed at creation) is backfilled per D8:
  `COALESCE(lower(recorded_range), earliest evidencing turn, lower(event_range))`.
* **`event_basis`** is inherited from `valid_from_basis`, which stays dual-written (supplying `event_basis` makes it
  authoritative). Both are fixed at creation.
* **T-28 watermark.** `clock_watermark(user_id, high)` is advanced by a `SECURITY DEFINER` trigger on every belief start or close,
  and a write that would put recorded time before it fails with `recorded_time_moved_backwards`, whatever `app.clock` says.
  The runtime role has no privilege on the table (the exact-grant matrix is unchanged); only the owner-run trigger writes it.
  The namespace is the user until `E-011` moves it to the scope. Equal instants are allowed (supersession closes and opens at one).
* **Rollback** (`rollback/009_clocks.sql`) restores version 8 exactly; the two columns come back at later attribute numbers.

Tested by `tests/integration/test_migration_m3.py`.

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
| **Invariant 1** | Constraint trigger `trg_memory_version_provenance` on `memory_version`, function `enforce_provenance()`, `DEFERRABLE INITIALLY DEFERRED` | Extended by migration 7 (`E-016`) with the lifecycle record obligations. Commit of `active`/`archived` requires ≥1 `memory_evidence` row or ≥1 outgoing `memory_derivation` row. `bitemporal_on_full.sql` disables it because that harness inserts believed rows without evidence, and re-enables it as its final statement, so a replay never leaves a persistent database with invariant 1 off. |
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
export CME_TEST_DATABASE_URL=postgresql://cme_test:cme_disposable_local_only@127.0.0.1:5433/cme_compatibility  # pragma: allowlist secret
uv run pytest -q tests/integration/test_full_schema.py
```

The test skips when `CME_TEST_DATABASE_URL` is absent. CI runs it against the pinned `pgvector/pgvector:0.8.7-pg16-bookworm` image (`D-25`).
