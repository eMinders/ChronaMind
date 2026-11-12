# Full schema (`RL-040`)

Canonical PostgreSQL DDL for the twelve tables in `TECHNICAL_SPECIFICATION.md` §3.1 and `Graduation_Project_Proposal.md` §8.

| File | Role |
|---|---|
| `001_full_schema.sql` | Migration 1: tables, invariants 1–4/7/9/10 constraints, `clock_now()`, `guard_update()`, RLS policies |
| `002_scope_grants.sql` | Migration 2: `scope_grant` (read-membership ACL, `V2-D36`) |
| `003_association_cap.sql` | Migration 3: `enforce_association_cap()` (`V2-D13`) |
| `004_tenant_integrity.sql` | Migration 4 (`V2-D54`): every intra-tenant foreign key composite on `(id, user_id)`; every engine function pinned to `search_path = <schema>, pg_catalog, pg_temp`; grants are never deleted |
| `005_axes.sql` | Migration 5 (`E-010`, M1 + M7): `kind`, `lifecycle`, `retention`, their backfill and the dual-write window |
| `006_facet_floors.sql` | Migration 6 (`E-020`, M8): the `um_facet` tag and the retention floor for claims and abstractions |
| `007_lifecycle_guard.sql` | Migration 7 (`E-016`): the closed lifecycle transition table, kind-conditioned legality, and the `I1′` derivation record |
| `008_source.sql` | Migration 8 (`E-013`, M4): evidence becomes a `(source_type, source_ref, span, content_hash)` reference, `turn` kept byte-identical and `external` outside the deletion boundary |
| `009_clocks.sql` | Migration 9 (`E-012`): three system clocks; `valid_range` renamed to `event_range`; `clock_watermark` per user |
| `010_structural_tables.sql` | Migration 10 (`E-014`, `E-016`): structural tables (`method`, `association`, `entity`, etc.) and lifecycle entry records |
| `011_scope.sql` | Migration 11 (`E-011`): `scope_path` and `visibility` on every scoped table; path-based RLS read policy; sub-scope grammar |
| `012_api_runtime.sql` | Migration 12 (`E-039`): `created_by`/`created_tier` on `memory_version`; `api_idempotency` table |
| `013_generic_predicates.sql` | Migration 13 (`E-039`): seeds `episode` and `statement` predicates |
| `014_entry_records.sql` | Migration 14 (`E-016`, `V2-D61`): the records of entry into the lifecycle (the gate decision at creation, the adjudication of a staged or held candidate) for every non-superuser role |
| `015_subscopes.sql` | Migration 15 (`E-011`, `V2-D64`): sub-scopes inside the owner's namespace; per-scope exclusion key (`I5`); per-scope belief watermark (`I4`) |
| `016_source_types.sql` | Migration 16 (`E-021`, `V2-D66`): admits declared tool, file, and event evidence source types |
| `017_declared_source_types.sql` | Migration 17 (`E-021`, `V2-D66`): stores tool, file, and event evidence under the declared type |
| `018_scope_entity_links.sql` | Migration 18 (`E-024`): `entity_link`, the authorized links between scope-local entities (no scope table, `V2-D65`) |
| `019_subject_entity.sql` | Migration 19 (`V2-D74`): `memory_version.subject_entity_id`, making an entity subject a composite foreign key (exists, same principal, same scope) |
| `020_outcome_stats.sql` | Migration 20 (`E-027`, `V2-D75`): `outcome_stats`, the procedure outcome counters `record_outcome` maintains. The minimal substrate `E-030` extends |
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
canonical tables stay unchanged; `_cme_schema_migrations` is a private migration-history
table recording version, filename, canonical-LF source SHA-256, live-catalog digest
and application time. Migrations **1–18** are registered in `migrate.MIGRATIONS` (append-only); integration suites build their
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
* **Records added later:** the `I1′` method reference and the adjudication, verification and retirement-reason
  records are migration 10's; the gate decision at creation and the adjudication of a staged or held candidate are
  migration 14's (`V2-D61`).
* **Dual-write allowances** (end with the window): staged `candidate`/`rejected` rows are born `retired` and a
  `candidate` may be promoted; a derived claim becomes `inferred` when its derivation row arrives; accepting a held
  derived/persona candidate activates it as `inferred`; v1 `persona` rows are exempt from the derivation record
  (narrowed by migration 14 to existing rows and the operator).
* **Decision (a)** (does `I10` apply to explicit withdrawal?) was left open by this migration and is decided by `V2-D59`:
  it applies, at no cost, because the withdrawal's recorded-time close is branch `(S)`.

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

## Migration 10: structural tables and the lifecycle records (`010_structural_tables.sql`, `E-014`, `E-016`, M6)

Six tables (`V2-D04`, `V2-D25`, `V2-D33`, `V2-D46`), each with forced row-level security. `E-011` (the scope hierarchy) is not built,
so the tenant tables carry the same per-user policy as the v1 tables and `E-011` migrates them; every foreign key between tenant
tables is composite on `(id, user_id)` and every function is pinned to the engine schema (`V2-D54`).

| Table | What the database guarantees |
|---|---|
| `association` | Ten closed types, same-tenant endpoints, no self-loop, weight in `[0,1]`, the degree cap of 32 (`V2-D13`, migration 3's function), and an `origin` (`tier_b` edges have no channel effect, `V2-D48`). Deleting an endpoint deletes the row and **never** cascades to the other endpoint. |
| `entity` | Opaque random ids (`ent_` + 32 hex), principal-scoped (`scope_path` inside the creator's `/u/<id>/`), invisible to every other user, indistinguishable from a missing id. Cross-scope identity is only an explicit `entity_link` (migration 18). |
| `method` | Shared registry, **append-only**: `UPDATE`, `DELETE` and `TRUNCATE` are refused; `(name, version)` is unique. Seeded with the v1 operators. The runtime role may read and append, nothing else. |
| `abstraction_stats` | The stored ledger: `o = s + k + u`, counts `>= 0`, `coverage` generated as `(s+k)/o`, **no `stale` column** (`V2-D24`); only an abstraction may have one; a `verified`/`refuted` status needs a `verification` record. |
| `verification` | Append-only held-out record with the support and held-out windows and context keys (`windows_disjoint` is derived) for `I11`. Append-only includes removal: `UPDATE`, `DELETE` and `TRUNCATE` are refused, so a `refuted` record cannot be deleted out from under the status that cites it. Only the `memory_version` cascade may remove a row. |
| `negative_constraint` | Sticky dissent: only a complete lift record (who, when, which new evidence) may be added, once. Deleting the dissented version clears its reference and leaves the dissent standing; deleting the lift evidence withdraws the lift, because an ungrounded lift is not a lift. |

**`E-016` record half.** `memory_version.method_id` is the method reference of `I1′` (set once, never changed). The provenance guard
(`enforce_provenance`, the deferred trigger of migration 1) now also requires a registered method for an active `inferred` row and
the ledger for an abstraction (`I15`). v1 derived claims get the v1 method of their derivation, and v1 persona rows get the
persona method and the zero ledger (`V2-D49` D7). A new deferred constraint trigger, `trg_memory_version_transition_records`
(listed in `engine.deletion.ARCHIVAL_GUARD_TRIGGERS`, so deletion judges the final state), requires the record of each
recorded transition of `ARCHITECTURE_V2` 2.3:

| Transition | Record required at commit |
|---|---|
| `contested -> asserted` | an `accept` decision in `memory_conflict` (the adjudication record) |
| `contested -> retired` | a `reject` decision in `memory_conflict` (the reason code) |
| `asserted`/`observed -> retired` | a closed recorded range (withdrawal, supersession) or an archival branch |
| `inferred -> retired` | one of those, or a refuting `verification`, or a dissent (`negative_constraint`), or a `reject` decision |
| `inferred -> contested` | a Tier C `contradicts` association |

The v1 staging promotion and the dual-write `-> inferred` corrections record nothing here (migration 7). The records of
*entry* into the lifecycle, the gate decision at creation among them, are migration 14's.

**The backfills run as the owner, and the owner is subject to `FORCE ROW LEVEL SECURITY`.** A migration applied by an
ordinary role matched zero rows in an unqualified `UPDATE`/`INSERT ... SELECT`, leaving every pre-existing inferred
artifact with a NULL `method_id` and every abstraction with no ledger - which then failed as
`inferred_requires_method_record` on the next write to that row. This migration lifts `FORCE` for its own statements,
fires the deferred provenance check and restores it, and `test_the_backfill_reaches_rows_when_the_owner_cannot_bypass_rls`
builds a version-9 schema owned by a `NOSUPERUSER NOBYPASSRLS` role and applies it as that role. A fresh install never
exposed it, because these tables are empty on a first apply.

**The same shape in migrations 5, 8 and 9 is fixed by the runner, not by them** (`V2-D60`): those sources are
checksum-bound, so `MigrationRunner` lifts `FORCE` on this schema's relations around every migration body and restores
it, judging any pending deferred events first, before recording the catalog digest. A populated schema is therefore
upgradeable by the ordinary owner that owns it, with no `BYPASSRLS` role. `clock_watermark`, which migration 9
deliberately leaves non-forced, is never forced by the runner either. Tested by
`tests/integration/test_migration_owner.py` (migrations 5, 8, 9 and 10, each upgrading a populated schema as a
`NOSUPERUSER NOBYPASSRLS` role).

> **Correction to the migration-7 header.** `007_lifecycle_guard.sql` says `E-014` extends a function called
> `enforce_lifecycle_records`. No such function exists: the record checks live in `enforce_provenance`, which this migration
> extends. A shipped migration is checksum-bound and cannot be edited, so the correction is recorded here.

Rollback: `rollback/010_structural_tables.sql` restores version 9 exactly. Tested by `tests/integration/test_migration_m6.py`.

## Migration 11: scope hierarchy and path-based RLS (`011_scope.sql`, `E-011`, M2 + M9)

Every scoped row gains `scope_path` and `visibility` (`private | project | shared`); existing rows are backfilled to
`/u/<user_id>/` and `private`. **`user_id` stays stored** (`V2-D49` D11) and a `CHECK` ties the path to it - the personal-root
equality here, relaxed to *inside* the owner's namespace by migration 15 - so a v1 writer that names only `user_id` still works (a
`BEFORE INSERT` trigger fills the personal root) and an inconsistent dual-write is refused. 15 tables are scoped: the ten of the plan, the three new association/ledger/verification tables, and `entity` and `negative_constraint`
(which already had a path and gain `visibility`). `app_user` is the principal table and `scope_grant` keeps its own policy.

* **Read policy** `<table>_scope_read` (`SELECT` only, `V2-D45`): own namespace, plus `project`/`shared` rows inside a scope an **active**
  `scope_grant` gives the caller; never `private` rows through a grant; `starts_with`, never `LIKE` (`/u/4/` does not match `/u/42/`, `_` is
  no wildcard). Writes stay governed by the existing own-user `<table>_isolation` policy, so holding a grant is read-only (`V2-D53`). The test
  `test_the_read_policy_is_the_library_expression_on_every_scoped_table` ties the SQL to `scope.access.rls_read_using`.
* **D12** the job idempotency key is unique **per user** (the global key was a cross-tenant existence oracle, `T-13`); the v1 test that asserted the
  collision is recorded in the test as superseded behaviour. **D13** `app_user.id` must be a valid scope segment (`/`, control characters, `.`/`..`,
  padding or length > 256 are refused), and the migration refuses to run over an existing id that is not.
* **M9** every scoped table carries a `<table>_scope_path_shape` `CHECK` (canonical trailing-separator path, lower-case sub-segments); on
  `entity` and `negative_constraint`, which admit sub-scopes, it is the only shape guard. The prefix index is built on `scope_path` with
  `text_pattern_ops` (the plan's generated `path_with_prefix` would be an exact copy); the planner uses it for `starts_with`.
* **Backfill under FORCE RLS.** The runner lifts `FORCE` around the body and restores it (`V2-D60`), so the backfill reaches every row for an
  owner that does not bypass RLS (`tests/integration/test_migration_owner.py`, case `[11]`); the backfill disables user triggers so
  provenance/clock/lifecycle guards are not re-run.
* **Only personal-root rows could exist** on thirteen of the tables until migration 15 admitted sub-scopes inside the owner's
  namespace (`V2-D64`); `entity` and `negative_constraint` already admitted them, so the whole `MA-011` universe, sub-scopes and
  sub-scope grants included, ran on the real `entity` table from this migration on. `scope.audit.record_scope_read` writes the
  content-free `scope_read` audit row (counts only) for a read that leaves the caller's namespace; see migration 15 for the path that
  actually calls it.

Rollback: `rollback/011_scope.sql` restores version 10 exactly (it refuses if two users now share a job key). Tested by
`tests/integration/test_migration_m2.py`.

## Migration 12: what the public API needs (`012_api_runtime.sql`, `E-039`)

* `memory_version.created_by` / `created_tier`: who created an artifact through the API and as which tier. Ownership for the
  `own_lineage_only` operations is the pair (principal, tier), because one principal acts both as Tier B (a cognition
  service) and as Tier C (the user). Both are NULL for v1 rows, which the API reads as "the user, as Tier C"; a `CHECK`
  makes them whole-or-nothing and a trigger fixes them once set.
* `api_idempotency (user_id, operation, idempotency_key, result_id)`: `API_V2` section 1, "a replay returns the original
  result and never a second effect" (`I9`). Per user, append-only, forced per-user RLS; the runtime role may read and append
  but never rewrite. It stores the engine-generated result id, never a request.

`api/runtime.py` and `api/handlers.py` wire ten operations through `Engine.call`: `register_method`, `create_entity`,
`link_entity`, `create_scope`, `link`, `unlink`, `adjudicate`, `retract_own`, `grant_access` and `revoke_access`. Every lookup and write
runs as the caller under the restricted role, so an id the caller may not read is absent to the engine itself.
`create_scope` (`E-040`) creates no row: a scope is its canonical path, and migrating 15 lets a scoped row lie anywhere
inside its owner's namespace, so the created path is immediately usable. `scope_grant` records read membership.
`link_entity` (`E-024`) writes migration 18's `entity_link`.

Rollback: `rollback/012_api_runtime.sql` restores version 11 exactly. Tested by `tests/integration/test_migration_m12.py`
and `tests/integration/test_api_wiring_structure.py`.

## Migration 13: the generic predicates (`013_generic_predicates.sql`, `E-039`)

Seeds `episode` and `statement` (both multi-valued) in `predicate_schema`. The V2 write operations take free content and no
predicate, but `memory_version.predicate_key` is a foreign key to this catalog, so they need entries to write under.
Multi-valued means two of them never supersede or conflict; a caller that wants supersession names a registered predicate
(the optional predicate, M5, is deferred past W1, `V2-D43`). An existing row under either key that defines it differently
refuses the migration (fail closed; `tests/integration/test_migration_m13.py`). Rollback: `rollback/013_generic_predicates.sql` (refuses while
any artifact uses them).

`api/ingest_handlers.py` wires `ingest_episode`, `assert` and `ingest_artifact`. An episode is recorded `observed` from a declared
source with no gate. A claim goes through the v1 validation gate (`engine.ingest.apply_candidate`) and is answered `asserted`,
`contested` or a `Rejected` result with the gate's static reason code. The answer is read back from what was recorded (the
first gate decision and the stored belief time), so a replayed key gets exactly the original answer. Only `turn` evidence can be grounded: an `external`
reference carries a hash, not the captured text, so asserting from external evidence alone is a `validation_error`.
Tested by `tests/integration/test_api_wiring_ingest.py`.

## Migration 14: the records of entry into the lifecycle (`014_entry_records.sql`, `E-016`, `V2-D61`)

Migrations 7 and 10 enforce the transitions *between* lifecycles and their records. This one enforces the creation rows of
`ARCHITECTURE_V2` 2.3 and the two v1 ways into the lifecycle that are not creations, at commit, with the deferred constraint
triggers `trg_memory_version_record_of_creation` (`AFTER INSERT`) and `trg_memory_version_record_of_entry` (`AFTER UPDATE`).
Both are listed in `engine.deletion.ARCHIVAL_GUARD_TRIGGERS`, and both are named so they fire after `trg_memory_version_provenance`
(a row with no evidence reports Invariant 1 first).

| Entry | Record required at commit |
|---|---|
| `- -> asserted` | evidence (migration 7) and an `accept` decision for the version in `memory_conflict`, unless it is a re-record (below) |
| `- -> contested` | a `hold` decision |
| `- -> inferred` | a derivation and a registered method (migrations 7, 10); now also for a persona row |
| `- -> observed` | evidence from a declared source (migrations 7, 8) |
| staged `candidate` -> `asserted`/`inferred` | an `accept` decision (the R4 promotion record `resolve_conflict_pending` writes) |
| staged `candidate` -> `contested` | a `hold` decision |
| `contested -> inferred` (a held derived or persona candidate accepted) | an `accept` decision and a derivation |

* **A re-record is not a new assertion.** Close-then-insert (supersession, boundary revision, multi-valued end) closes a belief at
  tick `c` and inserts its copy at `[c, NULL)`. The copy is admitted on the decision of the belief it continues when a version with
  the same memory key, subject, predicate, kind and content was closed at exactly `c` and the copy's event range lies within what was
  believed for that subject and predicate until `c`. New content or new event time is a new assertion. The ablations that *delete*
  the closed version (`A2`, `A2b`) leave nothing to continue and are refused under the application role (evaluation-only).
* **Who is bound:** every role that is not a superuser. A superuser can disable any trigger and bypasses row security, so no
  trigger binds it. The v1 test and evaluation harnesses connect as one and commit claims without the gate, and the v1
  archival and immutability tests pass unchanged. Every engine path the runtime role reaches records its decision in the
  same transaction.
* **The v1 persona exemption** from the derivation half of `I1′` (migration 7) now covers only existing persona rows and the
  operator. A persona row the application role creates or brings into the lifecycle needs a derivation.

Rollback: `rollback/014_entry_records.sql` restores version 13 exactly. Tested by `tests/integration/test_lifecycle_transitions.py`
(the creation records, the adjudicated entries, every engine write path under the runtime role, the re-record bounds, the
superuser scope, deferrability, rollback).

## Migration 15: sub-scopes, per-scope keys and the scope-keyed indexes (`015_subscopes.sql`, `E-011`, `V2-D64`)

Migration 11 admitted only the personal root on thirteen tables (`scope_path = '/u/' || user_id || '/'`) and deferred
everything below it to "the per-scope `I9` decision". This is that decision.

* **A row may lie anywhere inside its owner's namespace.** `<table>_scope_matches_user` becomes
  `<table>_scope_within_user`: `pg_catalog.starts_with(scope_path, '/u/' || user_id || '/')`. Exact by construction,
  because `D13` forbids the separator in `app_user.id` and in a scope segment: `/u/4/` still does not contain
  `/u/42/`, and a row can never be placed inside another tenant's namespace. What becomes possible is only what the
  profiles need - `/u/<id>/p/<repo>/` (coding), `/u/<id>/o/<org>/` (enterprise), `/u/<id>/p/<topic>/` (research),
  `/u/<id>/s/<task>/`. `authorize_write` is unchanged: a write still lands only in the caller's own namespace (`V2-D53`).
* **`I5` keys on the scope.** `no_bitemporal_overlap` moves from `user_id WITH =` to `scope_path WITH =`, so two
  sibling scopes of one user may each hold the same declared single-valued predicate, and a second open belief in the
  *same* scope is still refused (23P01). `V2-D49` D10 kept the `user_id` key while every scope was the personal root,
  where the two were equivalent; with sub-scopes they are not, and `I5` declares exclusivity per scope.
* **`I4` keys on the scope.** `clock_watermark` is keyed by `scope_path`, so a sibling scope cannot walk another
  scope's belief timeline backwards. The trigger derives the personal root when `scope_path` is still NULL, because
  `trg_memory_version_recorded_watermark` sorts before `trg_memory_version_scope_default` and must not depend on trigger
  order.
* **The indexes whose notes pointed here.** `memory_version_um_facet_idx` becomes `(scope_path text_pattern_ops, um_facet)`:
  facet-filtered reads are scope-filtered reads. `<table>_scope_path_idx` (migration 11) already keys on `scope_path`.
* **`/o/<org>/` is a sub-scope, not a namespace.** §13 gives the enterprise profile the root `/o/<org>`; the implemented
  form is `/u/<owner>/o/<org>/`, shared through `scope_grant`. A top-level `/o/` is refused, because `scope_grant.owner_user_id`
  references `app_user` and the read policy matches `/u/<caller>/`: an `/o/` row would have no owner to grant it and no policy
  to return it.
* **No existing row changes.** Every row is in its personal root, for which the new `CHECK` holds; re-keying the exclusion
  constraint and the watermark preserves every key that existed.

The content-free `scope_read` row of rule 6 is written by the public entry point, not by this migration:
`api.entry.Engine.call` reports every scope path a call resolved outside the caller's namespace to a `ScopeReadRecorder`, and
`api.wiring.build_engine` wires `scope.audit.ConnectionScopeReads` so a deployment cannot omit it.

Rollback: `rollback/015_subscopes.sql` restores version 14 exactly (byte-identical catalog fingerprint, including the
exclusion index's legacy `valid_range` attribute, which a table rename does not propagate). It **refuses** if any row
lies in a sub-scope, because version 14 cannot represent one and flattening it into the owner's root would merge two
scopes' beliefs. Tested by `tests/integration/test_migration_m15.py`.

## Migration 18: scope-local entity links (`018_scope_entity_links.sql`, `E-024`)

One table, `entity_link`: an explicit, authorized relationship between two of one principal's entities, which may lie
in different scopes. Entities stay scope-local and opaque (`ARCHITECTURE_V2.md` 5.2); a link records that two ids
denote the same referent and **merges nothing**, so neither entity's claims or attributes become readable from the
other scope. Both endpoint keys are composite foreign keys on `(id, user_id)`, so a link can never cite another
principal's entity; the pair is stored in one orientation (`entity_a < entity_b`) under a unique relation, so a replay
is the same row. Forced RLS, and the least-privilege `SELECT, INSERT` grant of `security/rls.sql` (`RL-072`).

**No scope table is added.** A scope *is* its canonical path (`V2-D65`): `scope_grant` records read membership and
every scoped row carries its own `scope_path`, so a `scope` table would duplicate that path, need its own RLS and
consistency checks against thirteen tables, and need a cascade story for revocation. Migration 15 already admits a
scoped row anywhere inside its owner's namespace, so a created sub-scope holds entities, associations and memories
with no row of its own, and `create_scope` stays validation plus an idempotency entry plus a content-free audit row
(`E-040`).

`link_entity` is the operation this migration unblocks (`api/handlers.py`): `targets_readable` requires both endpoints
in the caller's read set, and an unreadable endpoint answers exactly like an absent one (`forbidden`, T-09). The
optional `subject_entity` input of `assert` (`E-024`) records which entity a claim is about; migration 19 makes
the same-scope rule a composite foreign key rather than only a handler check.

Rollback: `rollback/018_scope_entity_links.sql` restores version 17 exactly and **refuses** while any link exists,
because removing it would destroy cross-scope identity. Tested by `tests/integration/test_cross_domain_entity.py`.

## Migration 19: an entity subject is a foreign key (`019_subject_entity.sql`, `V2-D74`)

`memory_version.subject_entity_id` is typed: `NULL` means the subject is `subject_key` itself (`'self'` for every v1
claim), and a value means the subject *is* that entity. `subject_key` keeps holding the same id because it is the
column `no_bitemporal_overlap` and the supersession rules compare.

One composite foreign key `(subject_entity_id, user_id, scope_path) → entity (id, user_id, scope_path)` makes three
properties structural at once: the entity exists, it belongs to the same principal, and it lies in the claim's own
scope. `ON DELETE RESTRICT`, because a cited entity is part of the claim (a migration-18 `entity_link` cascades
instead: the link *is* the relation). `CHECK (subject_entity_id IS NULL OR subject_entity_id = subject_key)` keeps
the two spellings from disagreeing, and `guard_update` treats the column as belief content, so a claim cannot be
re-pointed at another entity in place.

Nothing is backfilled: no row written before this migration recorded an entity subject, and inferring one from
`subject_key` would fabricate a link the caller never made. The rollback restores version 18's catalog fingerprint
exactly, with `guard_update` restored byte-for-byte and its pinned `search_path` re-applied.

## Migration 20: procedure outcome counters (`020_outcome_stats.sql`, `E-027`, `V2-D75`)

`ARCHITECTURE_V2.md` §6 gives a procedure four fields. Only `outcome_stats` has a reader today — the ranking term in
`engine/procedure_ranking.py` — so only it is built. `preconditions` and `steps` are **`E-030`'s** and are deliberately
absent rather than sketched, because a half-built procedure shape is what `E-030` would have to rewrite.

A **side table**, not columns on `memory_version`, following `abstraction_stats`: counters move at a different rate,
under different rules, than belief content. It also leaves the `EXCLUDE`-constrained bitemporal core untouched.

| Column | Meaning |
|---|---|
| `successes`, `failures` | counters, `>= 0`; the rate is derived at read time, never stored (`V2-D75`) |
| `last_used_at` | the `clock_now()` of the most recent recorded outcome |
| `cost_count`, `cost_sum` | the cost ledger; `mean_cost` is generated as `cost_sum / cost_count`, `NULL` when count is 0 |

`mean_cost` is sum-over-count rather than a stored running mean so the persisted value cannot depend on the order
outcomes arrived in. A recorded cost of `0.0` is a real mean and there is deliberately **no** constraint pairing count
with sum, because `count=1, sum=0` is a legitimate row.

`guard_outcome_stats` enforces two rules the schema should own rather than one handler: the target must be
`kind='procedure'` (mirroring `guard_abstraction_stats`), and the counters are **monotone** — an outcome is a recorded
event, so rewriting one would rewrite history (`I3`). Forced RLS with read/insert/update policies; the function's
`search_path` is pinned after creation (`RL-072`).

Rollback: `rollback/020_outcome_stats.sql` restores version 19 exactly and drops every recorded outcome, which version
19 has nowhere to keep. It also drops `guard_outcome_stats` explicitly (not table-owned) and deletes the version-20
history row, so `status()` still sees a gap-free prefix and reports `pending` rather than `drifted`.

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
| **Invariant 1** | Constraint trigger `trg_memory_version_provenance` on `memory_version`, function `enforce_provenance()`, `DEFERRABLE INITIALLY DEFERRED` | Extended by migration 7 (`E-016`) with the lifecycle record obligations; the records of every lifecycle transition (migration 10) and of entry into the lifecycle (migration 14) are separate deferred triggers, which `bitemporal_on_full.sql` steps aside the same way. Commit of `active`/`archived` requires ≥1 `memory_evidence` row or ≥1 outgoing `memory_derivation` row. `bitemporal_on_full.sql` disables it because that harness inserts believed rows without evidence, and re-enables it as its final statement, so a replay never leaves a persistent database with invariant 1 off. |
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
