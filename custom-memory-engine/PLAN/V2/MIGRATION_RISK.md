# Migration Risk Review (M1–M9)

**Task:** `MA-016` · **Status:** design review; no migration is implemented yet. Updated in r8 against `main`
after the r7 decisions (measurements re-run on PostgreSQL 16.15 with the r8 schema; section 4). **Governs:** `E-010`,
`E-011`, `E-012`, `E-013`, `E-014`, `E-020`. **References:** `ARCHITECTURE_V2.md` section 12 (the migration
table), section 3 (clocks), section 5 (scope), `INVARIANT_LEDGER.md`, `THREAT_MODEL.md`.

For each of the nine migrations this document gives: preconditions, the statement list (one transaction),
the failure modes, the rollback, the verification command, and whether it can run online. Claims marked
**measured** come from a dry run on a scratch schema in the disposable PostgreSQL 16.15 / pgvector 0.8.7
database (commands are in section 4). Everything else is analysis and is labelled as such.

## 1. Cross-cutting facts that change every migration

| # | Fact | Consequence |
|---|---|---|
| X1 | **Resolved (r7, `29e63a3`):** `schema/migrate.py` is multi-version: an ordered `MIGRATIONS` registry (001 full schema, 002 `scope_grant`, 003 association cap), per-version source checksums, a `pending` state, and refusal of edited historical sources (`tests/integration/test_migrations.py`). | Each of `M1`-`M9` is a new registry entry with its own checksum; the first W1 migration (`E-010`) owns extending the registry for its version, as every later task does for its own. |
| X2 | The catalog fingerprint covers columns, constraints, triggers, functions and policies. | Every migration changes it. `status` reports `drifted` until the registry records the new digest. |
| X3 | PL/pgSQL bodies are **late-bound text**. `guard_update`, `enforce_archival_invariant` and `guard_derivation_coverage` reference `valid_range`; `guard_update` also compares `layer`, `user_id`, `valid_from_basis`. | A rename or column swap does not update them and fails only when the trigger fires. **Measured:** forgetting to recreate `guard_update` after the M3 rename gives `UndefinedColumn: record "new" has no field "valid_range"` on the first update. |
| X4 | `guard_update` compares a fixed tuple of 14 columns and rejects any change. | A backfill `UPDATE` of **new** columns passes. Any change to a guarded column (rename, type, generated) needs the function recreated in the same transaction. A new column that must be immutable has to be **added to that tuple** or it silently becomes mutable. |
| X5 | Engine SQL is plain text. `valid_range` appears on 54 lines across `engine/transactions.py` (41), `render.py` (6), `predicates.py` (4), `gate.py`, `retrieval.py`, `consolidator.py`, plus 9 test files, `scripts/bench_retrieval.py` and `schema/bitemporal_on_full.sql`. | A column rename is a code change as well as a DDL change; both must land in one PR or the Tier-A wall goes red. |
| X6 | `provision_runtime_role` grants on a fixed list of **12** tables since r7 (11 v1 tables + `scope_grant`; `security/provisioning.py`, `migrate.TABLES`). | New tables from `M6` must be added to the grants and to the forced-RLS verification, or the restricted role cannot use them. |
| X7 | `CREATE INDEX CONCURRENTLY` cannot run inside a transaction; `EXCLUDE` constraints cannot be built concurrently. | "Single transaction" and "online" conflict for index-heavy steps; the table below says which. |

### 1.1 Decision register

Every decision this review needs. Status **approved** means a recorded `V2-Dnn` exists on `main`; **proposed**
means a new `V2-Dnn` is requested from the Project Owner and is not yet in force. Nothing here is applied by this
document.

| # | Decision | Status | Record / proposal | Affects |
|---|---|---|---|---|
| D1 | Multi-version runner contract and owner | **approved / done** | r7 `29e63a3` (X1); each migration's task extends the registry | all |
| D2 | v1 `status` → `lifecycle`/`retention`, incl. staging rows `candidate`/`rejected` (→ `retired`, reason-coded) | **approved** | `V2-D38` (`model.axes.map_v1_version`, `V1_BACKFILL_SQL`) | `E-010` |
| D3 | `archived` as a "retention value" (M7) | **approved** | `V2-D38` (`archived` → `retired` + `durable`); owner `E-010` per `V2-D43` | `E-010` |
| D4 | `M5` optional `predicate_key` | **approved: deferred past W1** | `V2-D43` | — |
| D5 | Prefix matching in `M9` policies | **approved** | `V2-D35` (`starts_with`, never `LIKE`); the policies enforce the ACL from `app.user_id` and `scope_grant`, not a selector GUC (`V2-D45`, `scope.access.rls_read_using`) | `E-011` |
| D6 | `layer` is many-to-one onto `kind` (M1 finding 1) | **proposed** | Keep `layer` **stored and dual-written** until the last v1 reader is removed; it is the lossless discriminator for `semantic` vs `opinion` | `E-010` |
| D7 | Backfilled `persona` rows (section 2.1) | **proposed** | `abstraction/inferred` per `V2-D38`, plus a zero ledger (`s = k = u = o = 0`, `verification_status = unverified`) created by `E-014`; `o = 0` is "insufficient evidence", so they can never be verified or rendered in `facts` | `E-010`, `E-014` |
| D8 | `observed_at` backfill source (M3) | **proposed** | `lower(recorded_range)` for believed rows; the evidencing turn's `occurred_at` for rows with `recorded_range IS NULL` | `E-012` |
| D9 | M3 rename in place vs drop-and-recreate | **proposed** | Rename in place (measured equivalent; 3.2 ms vs 48 s `ACCESS EXCLUSIVE` at 400k rows). This **changes #63's literal steps 1-3** and needs owner sign-off | `E-012` |
| D10 | Exclusion-constraint key after M2: `user_id` or scope | **proposed** | Keep `user_id` in W1 (v1-equivalent for the conversational case); switch to the scope key only with the per-scope `I9` decision | `E-011`, `E-012` |
| D11 | `user_id` as a **generated** column (M2, `ARCHITECTURE_V2.md` section 12) | **proposed: do not generate** | Measured (section 2, M2): a generated `user_id` supports every dependent but breaks every v1 `INSERT` that names `user_id`, which rules out a dual-write window. Keep `user_id` **stored** with `CHECK (scope_path = '/u/' \|\| user_id \|\| '/')` while the scope is personal. This amends section 12's wording | `E-011` |
| D12 | Global `job.idempotency_key` uniqueness (oracle, `THREAT_MODEL.md` T-13) | **proposed** | Scope it by the `I9` key in `M2` | `E-011` |
| D13 | `app_user.id` must be a valid scope user segment | **approved** (`V2-D45`) | v1 ids may contain `/` (`security.validate_user_id` allows it); `'/u/' \|\| id \|\| '/'` would then forge a path inside another tenant's subtree. M2 refuses to run if any id fails `scope.paths.user_scope`, and adds `CHECK` `app_user_id_is_scope_segment` so no such id can be created afterwards | `E-011` |

## 2. Per-migration review

### 2.0 Column order (#59 section 3.8)

Every migration **appends** columns (`ALTER TABLE … ADD COLUMN` always appends in PostgreSQL). The positional
`INSERT`s in `validation/bitemporal_checks.sql` and `schema/bitemporal_on_full.sql` rely on columns 1-10 of
`memory_version` keeping their order, so no migration may drop and re-add an existing column of those ten. The
only rename (M3, `valid_range` → `event_range`) keeps the column's position, and D11 keeps `user_id` in place.
A catalog assertion in each migration test should compare `attnum` of columns 1-10 before and after.

### 2.1 `persona` rows (#59 section 3.5)

v1 accepts `layer = 'persona'` from callers (`engine/transactions.py` `MemoryLayer`), so persona rows may exist in
v1 data even though no built-in extractor writes them. `V2-D38` maps an active persona row to
`kind = abstraction`, `lifecycle = inferred`. Abstractions require a ledger (`STATISTICS.md` section 2.3, `I15`)
that v1 never recorded, so a backfilled persona row would violate `I15` the moment `M6` adds the ledger table.
Proposal D7 gives each one an explicit zero ledger: honest (nothing was measured), satisfiable
(`o = s + k + u` holds), and safe (`o = 0` never passes the admission predicate, so the row stays in
`hypotheses`). Count them before `E-010`: `SELECT count(*) FROM memory_version WHERE layer = 'persona'`.

### 2.2 Order (#59 section 3.9)

```text
M1 + M7 (E-010) ──► M2 + M9 (E-011) ──► M3 (E-012) ──► M4 (E-013) ──► M6 (E-014) ──► M8 (E-020)
                                                    M5: deferred (V2-D43)
```

`M1` comes first because every later migration and the lifecycle work read the axes. `M2`/`M9` precede `M3`, so
that the recreated or renamed exclusion constraint is final in one step (D10). `M4` needs only `M1`. `M6` needs
`M2` because every new table carries a scope and an RLS policy. `M8` reads `kind` and `retention` and must exist
before any W2 working-memory feature. This matches the W1 task dependencies in #61-#65 and #71.

### 2.3 Per migration

Format: **Pre** preconditions · **Txn** statements in the single transaction · **Fail** failure modes ·
**Rollback** · **Verify** command · **Online?**

### M1 — `kind`, `lifecycle`, `retention` (`E-010`)

- **Pre:** `v1.0.0` tagged (`E-002`); queue drained or paused; runner registry from X1.
- **Txn:** (1) `ALTER TABLE memory_version ADD COLUMN kind text, ADD COLUMN lifecycle text, ADD COLUMN retention text` (metadata-only); (2) batched backfill `UPDATE` of the three columns; (3) `ADD CONSTRAINT … CHECK (kind IN (...)) NOT VALID`, then `VALIDATE CONSTRAINT` (no long lock); (4) recreate `guard_update` with `kind` in the immutable tuple (X4); (5) switch engine inserts to dual-write (`layer` **and** the new columns).
- **Fail (analysis):**
  1. **The backfill map is many-to-one.** `semantic` and `opinion` both map to `claim`, so `layer` **cannot be regenerated** from `(kind, lifecycle, retention)`. The acceptance criteria ask for both "`layer` remains readable as a generated compatibility column" and "reversible". Those are only compatible if a lossless discriminator is kept (a stored `legacy_layer`, or `layer` stays a stored column until sunset). **Decision needed before `E-010`.**
  2. **`status` has no lifecycle image for staging rows.** `candidate`, `rejected` and `archived` are v1 statuses with no value in the closed lifecycle table (`ARCHITECTURE_V2.md` 2.3, which begins at "—"). The backfill spec covers `layer` only. A mapping (or an explicit decision that staging rows stay outside the lifecycle) is required.
  3. `inferred` is decided "by derivation presence", which needs a join on `memory_derivation`; rows with only a `corroboration` edge need an explicit rule.
  4. Backfill rewrites every row (bloat, row locks); each updated row re-fires the deferred provenance trigger. Concurrent supersession takes a per-user advisory lock, but a backfill does not, so batches must take the same lock or tolerate `40001`.
- **Rollback (one transaction):**
  ```sql
  DROP TRIGGER IF EXISTS trg_memory_version_axes_dual_write ON memory_version;
  DROP FUNCTION IF EXISTS memory_version_axes_dual_write();
  CREATE OR REPLACE FUNCTION guard_update() … ;   -- the pre-M1 body (without `kind`), from 001_full_schema.sql
  ALTER TABLE memory_version DROP CONSTRAINT memory_version_kind_check,
    DROP CONSTRAINT memory_version_lifecycle_check, DROP CONSTRAINT memory_version_retention_check;
  ALTER TABLE memory_version DROP COLUMN kind, DROP COLUMN lifecycle, DROP COLUMN retention;
  DELETE FROM _cme_schema_migrations WHERE version = <M1 version>;
  ```
  Safe while `layer` is stored (D6); once `layer` is dropped, the rollback cannot restore `semantic` vs `opinion`.
- **Verify:** `tests/integration/test_migration_m1.py` (from `E-010`); `SELECT count(*) FROM memory_version WHERE kind IS NULL OR lifecycle IS NULL OR retention IS NULL` returns `0`; the Tier-A wall stays green.
- **Online?** Yes for steps 1-3 (batched, `NOT VALID` then `VALIDATE`); the `guard_update` swap is a brief lock.

### M2 — scope hierarchy (`E-011`, with `M9`)

- **Pre:** `MA-011` decisions recorded (`V2-D35`, `V2-D36`, `V2-D45`). D13 preflight: every existing `app_user.id` is a valid user segment. The check must return no rows: `SELECT id FROM app_user WHERE id ~ '[/[:cntrl:]]' OR id IN ('.', '..') OR id <> btrim(id) OR id = '' OR length(id) > 256` (a `/` in an id would forge a path, T-03).
- **Coverage (#59 section 3.6, measured on the r8 catalog):** 12 RLS-protected tables. **Ten carry `user_id`** and each gets `scope_path` + `visibility`: `memory_version`, `memory_evidence`, `memory_derivation`, `memory_embedding`, `memory_conflict`, `turn`, `session`, `job`, `audit_event`, `deletion_request`. Dependents of `user_id` there: 10 foreign keys to `app_user`, 10 isolation policies, `job_extraction_idempotent` (UNIQUE), `no_bitemporal_overlap` (EXCLUDE), and indexes `memory_version_temporal_idx`, `memory_version_memory_key_idx`, `memory_version_open_belief_idx`, `memory_derivation_source_idx`. `app_user` is the principal table (its policy keys on `id`; no scope column), and `scope_grant` keys on `owner_user_id`/the grantee (already scope-aware, r7).
- **Txn (with D11):** (1) on each of the ten tables, add `scope_path text`, `visibility text`; (2) backfill `scope_path = '/u/' || user_id || '/'`, `visibility = 'private'`; (3) `CHECK (scope_path = '/u/' || user_id || '/') NOT VALID` then `VALIDATE`; (4) the path-shape `CHECK` and `path_with_prefix` (M9); (5) `ALTER TABLE app_user ADD CONSTRAINT app_user_id_is_scope_segment CHECK (id !~ '[/[:cntrl:]]' AND id NOT IN ('.', '..') AND id = btrim(id) AND id <> '' AND length(id) <= 256)` (D13); (6) rewrite the ten isolation policies to `USING (rls_read_using('<table>'))`, the ACL predicate (D5, `V2-D45`; the table name qualifies the row columns, which is mandatory); `user_id` stays stored, so its FKs, UNIQUE, EXCLUDE and indexes are untouched.
- **Fail (analysis):**
  1. PostgreSQL 16 cannot turn an existing column into a generated one. A generated `user_id` must **replace** the stored one (`DROP COLUMN … CASCADE`, re-add, recreate dependents). **Measured (PostgreSQL 16.15, rolled-back transaction on the full r8 schema):** a plain `DROP COLUMN user_id` is refused (`policy job_isolation … depends on column user_id`); after the swap, a `STORED` generated `user_id = split_part(scope_path, '/', 3)` **does** carry the foreign key (a row for an unknown user is rejected with `job_user_id_fkey`), the `job_extraction_idempotent` UNIQUE, the RLS policy, the `no_bitemporal_overlap` GiST exclusion constraint and the temporal index.
  2. **Measured, and decisive for D11:** with a generated `user_id`, a v1-style `INSERT` that names `user_id` fails: `cannot insert a non-DEFAULT value into column "user_id"`. 23 `INSERT` statements in 8 engine files name it, so a generated column forces every v1 write path to change in the same migration, with no dual-write window. The stored-plus-`CHECK` alternative was measured too: a v1 `INSERT` without `scope_path` succeeds, a consistent dual-write succeeds, and an inconsistent one (`user_id = 'u42'`, `scope_path = '/u/u4/'`) is rejected by the `CHECK`.
  3. Policies on all 11 user tables reference `user_id`; changing only `memory_version` leaves ten tables on the old predicate.
  4. Backfill collisions are impossible when ids contain no `/`; ids that do would silently create paths under another tenant's subtree.
- **Rollback (D11 variant; per table `t` of the ten, in one transaction):**
  ```sql
  DROP POLICY t_scope_isolation ON t;
  CREATE POLICY t_isolation ON t USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
    WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));
  ALTER TABLE t DROP CONSTRAINT t_scope_matches_user;
  ALTER TABLE t DROP COLUMN path_with_prefix, DROP COLUMN visibility, DROP COLUMN scope_path;
  -- once, after the ten tables:
  ALTER TABLE app_user DROP CONSTRAINT app_user_id_is_scope_segment;
  DELETE FROM _cme_schema_migrations WHERE version = <M2 version>;
  ```
  No data is lost: `scope_path` is derivable from `user_id` while every scope is personal.
- **Verify:** `tests/integration/test_scope_isolation.py` (`MA-011`); Suite `I` reports `0` exposures; `SELECT count(*) FROM memory_version WHERE path <> '/u/' || user_id || '/'` returns `0`.
- **Online?** **No.** Table rewrite plus rebuilding the GiST exclusion constraint under `ACCESS EXCLUSIVE` (compare the **measured** M3 timings below).

### M3 — `valid_range` → `event_range`, `observed_at`, `event_basis` (`E-012`)

Highest risk in the programme. **Measured** on a scratch schema (section 4).

- **Pre:** `MA-016` (this document); no open transactions; engine and test SQL updated in the **same** pull request (X5).
- **Txn (one transaction, in this order):**
  1. *(recreate variant only)* `ALTER TABLE memory_version DROP CONSTRAINT no_bitemporal_overlap;`
  2. `ALTER TABLE memory_version RENAME COLUMN valid_range TO event_range;` (and `valid_from_basis` → `event_basis`)
  3. *(only if the owner keeps the recreate, see the decision below)* `ALTER TABLE memory_version ADD CONSTRAINT no_bitemporal_overlap EXCLUDE USING gist (user_id WITH =, subject_key WITH =, predicate_key WITH =, event_range WITH &&, recorded_range WITH &&) WHERE (single_valued AND status IN ('active','archived'));`
  4. `ALTER TABLE memory_version ADD COLUMN observed_at timestamptz;` then backfill (rule needed: `lower(recorded_range)` for believed rows; the evidencing turn's `occurred_at` for staging rows).
  5. `CREATE OR REPLACE FUNCTION guard_update, enforce_archival_invariant, guard_derivation_coverage` with the new names (the three functions found by the dry run).
- **What a plain rename already carries (measured):** the `valid_wellformed` CHECK, the
  `memory_version_temporal_idx` index **and the `no_bitemporal_overlap` exclusion constraint** follow
  `RENAME COLUMN` on their own (PostgreSQL stores column references by number). After a rename-only
  transaction `pg_get_constraintdef` already shows `event_range WITH &&`. A rename-only transaction took
  **3.2 ms at both 100,000 and 400,000 rows**, because it is a catalog change with no index rebuild.
- **Decision needed for `E-012` (recommended: rename in place).** The architecture and the `E-012` text specify
  drop, rename, **recreate**. Recreating buys no semantic change (the definition is identical, below) and costs a
  full GiST rebuild under `ACCESS EXCLUSIVE` (7.4 s at 100k rows, 48 s at 400k). Recommendation: rename in
  place, keep step 5, and satisfy "recreated with identical semantics" with the catalog comparison and the two
  replays in **Verify**. If the owner prefers the literal drop-and-recreate (for example to rebuild a bloated
  index), it must be scheduled as an offline window.
- **Fail (measured):**
  - Omitting step 5 leaves `UndefinedColumn` bombs in three triggers.
  - The recreated constraint is **identical modulo the column name** (`pg_get_constraintdef` compared).
  - After the change, no function body still mentions `valid_range`.
- **Rollback (measured inside a rolled-back transaction; after commit, run as one transaction):**
  ```sql
  ALTER TABLE memory_version RENAME COLUMN event_range TO valid_range;
  ALTER TABLE memory_version RENAME COLUMN event_basis TO valid_from_basis;
  ALTER TABLE memory_version DROP COLUMN observed_at;
  CREATE OR REPLACE FUNCTION guard_update() … ;                 -- pre-M3 bodies from 001_full_schema.sql
  CREATE OR REPLACE FUNCTION enforce_archival_invariant() … ;
  CREATE OR REPLACE FUNCTION guard_derivation_coverage() … ;
  DELETE FROM _cme_schema_migrations WHERE version = <M3 version>;
  ```
  With rename in place (D9) the exclusion constraint, `valid_wellformed` and `memory_version_temporal_idx` follow the
  rename back automatically. With drop-and-recreate, the inverse also drops and recreates `no_bitemporal_overlap` on
  `valid_range` (an offline step, timings above). The architecture's "reversible via view" would be a
  `memory_version_v1` compatibility view; a view cannot reuse the table's own name.
- **Verify:** the replay target is **`schema/bitemporal_on_full.sql`** (43 assertions against the real
  `memory_version`, run by `tests/integration/test_full_schema.py` and `test_migrations.py`), adapted to
  `event_range` in the same pull request; it must produce 43 `PASS` rows on the migrated schema (**measured**: the
  substituted script does). `validation/bitemporal_checks.sql` stays **unchanged**: it builds its own `bt` schema
  with a hard-coded `valid_range` and never touches the engine schema, so it proves nothing about M3; it must
  still pass as the frozen v1 asset. Also verify `pg_get_constraintdef` equality modulo the column name, and a
  behavioural `23P01` on two overlapping single-valued believed versions, before and after.
  **Wording discrepancy (recorded, not fixed here):** `ROADMAP_V2.md` `E-012` and `ARCHITECTURE_V2.md` section 12
  M3 both name `validation/bitemporal_checks.sql` as the replay; #63 (r5) corrects this to `bitemporal_on_full.sql`.
- **Online?** Depends on the decision above. **Rename in place: effectively yes** (a millisecond-scale `ACCESS EXCLUSIVE`, but code and database must change together, since the engine's SQL names the column, X5). **Drop-and-recreate as specified: no.** It holds `ACCESS EXCLUSIVE` on `memory_version` while rebuilding a GiST index; **measured 0.46 s at 10,000 rows, 7.4 s at 100,000, 48 s at 400,000** (containerised development database). It blocks reads and writes for that time, so schedule it as a maintenance window and size the table first.

### M4 — generalised `SOURCE` (`E-013`)

- **Pre:** `E-010`. Inventory every join on `memory_evidence.turn_id` (`deletion.py`, queue purge by `turn_id`, `enforce_provenance`).
- **Txn:** add `source_type`, `source_ref`, `span`, `content_hash`; backfill `('turn', turn_id, evidence_span)` with `content_hash = sha256(turn.content)`; keep `turn_id` and its foreign key until sunset; rebuild the primary key on the new tuple; recreate `enforce_provenance` if its body changes.
- **Fail (analysis):** replacing `turn_id` removes the foreign key that today prevents dangling evidence, and one column cannot be a foreign key for only some rows. Keep a nullable `turn_id` with the key and a `CHECK (source_type <> 'turn' OR turn_id IS NOT NULL)`. `type='external'` content lies **outside** the deletion boundary and the closure cannot remove it (must be documented, as the acceptance criteria require). The queue's "purge jobs for deleted turns" logic must learn `source_ref`.
- **Rollback (refuses when non-turn sources exist, #64 section 3.6):**
  ```sql
  DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM memory_evidence WHERE source_type <> 'turn') THEN
      RAISE EXCEPTION 'm4_rollback_refused_non_turn_sources_exist';
    END IF;
  END $$;
  CREATE OR REPLACE FUNCTION enforce_provenance() … ;           -- pre-M4 body
  ALTER TABLE memory_evidence DROP CONSTRAINT memory_evidence_turn_source_check;
  ALTER TABLE memory_evidence DROP COLUMN content_hash, DROP COLUMN span,
    DROP COLUMN source_ref, DROP COLUMN source_type;
  DELETE FROM _cme_schema_migrations WHERE version = <M4 version>;
  ```
- **Verify:** `tests/integration/test_migration_m4.py`; evidence rows byte-identical (`md5` over the old columns before and after); a tool-call episode round-trips with provenance.
- **Online?** Mostly: columns and backfill in batches; the primary-key rebuild needs a short lock (build the unique index `CONCURRENTLY` first, outside the transaction, per X7).

### M5 — optional `predicate_schema`

- **No owning task.** `E-010`-`E-020` never mention it (see 3.1).
- **Txn:** `ALTER TABLE memory_version ALTER COLUMN predicate_key DROP NOT NULL`; the foreign key stays.
- **Fail (analysis):** `no_bitemporal_overlap` lists `predicate_key WITH =`. `NULL = NULL` is not true, so **rows with no predicate never conflict** and single-valued exclusivity is silently off for them. That is the stated intent (the conflict detector becomes the arbiter) but it weakens `I5` and must be an explicit decision, with the detector's error rate measured, not assumed.
- **Rollback:** `ALTER TABLE memory_version ALTER COLUMN predicate_key SET NOT NULL;` fails once any `NULL` row exists, so this migration is **effectively one-way** after the first predicate-less row. **Deferred past W1 (`V2-D43`, D4).**
- **Verify:** a test that two predicate-less rows with overlapping ranges are accepted (documenting the weakened guarantee) and that rows with predicates still conflict.
- **Online?** Yes (metadata-only).

### M6 — structural tables (`E-014`)

- **Txn:** `CREATE TABLE ASSOCIATION, ENTITY, METHOD, ABSTRACTION_STATS, VERIFICATION, NEGATIVE_CONSTRAINT` (`METHOD` is the single physical name, `ARCHITECTURE_V2.md` section 2.1); `ENABLE` and `FORCE ROW LEVEL SECURITY` plus an isolation policy on each; an append-only trigger on `METHOD`; **no** cascade from `ASSOCIATION` to associated memories.
- **Fail (analysis):** a table created without `FORCE ROW LEVEL SECURITY` is a cross-tenant read (X6, `INVARIANT_LEDGER.md` `I7`). `ENTITY` ids must be opaque or the existence oracle in `THREAT_MODEL.md` T-07 appears. Add each table to `USER_TABLES` in `security/provisioning.py` so the runtime role and `verify_forced_rls` cover it.
- **Rollback (new tables only; reverse dependency order):**
  ```sql
  DROP TABLE negative_constraint, verification, abstraction_stats, association, entity;
  DROP TABLE method;                                   -- referenced by the tables above
  DELETE FROM _cme_schema_migrations WHERE version = <M6 version>;
  ```
  and remove the six tables from `USER_TABLES` / the runtime-role grants in the same change.
- **Verify:** `tests/integration/test_migration_m6.py`; deleting a memory removes its associations without deleting associated memories; `verify_forced_rls` passes for the new tables.
- **Online?** Yes (new objects).

### M7 — `archived` compatibility (`E-010`, `V2-D38`/`V2-D43`)

- **Decided (D3):** v1 `archived` maps to `lifecycle = 'retired'`, `retention = 'durable'`; owned by `E-010` and
  applied by the `M1` backfill (`V1_BACKFILL_SQL`).
- **Fail (analysis):** two meanings coexist: v1 `status='archived'` with `archival_branch` and the `(S) ∨ (C)` trigger, and V2 `retention` + `retired`. The `(S) ∨ (C)` guarantee (Invariant 10) must keep holding for the v1 value while new code stops using it, or answerability can be lost silently.
- **Rollback:** M7 has no DDL of its own; it is part of M1's backfill, so M1's rollback statements remove it.
  The v1 `status = 'archived'` values are never rewritten, so nothing else needs restoring.
- **Verify:** the existing `test_r7_end_multivalued_and_r5_invariant_10_d27_archival` and `test_archival_guard_verification_fails_closed_when_triggers_are_absent` stay green.
- **Online?** Yes.

### M8 — `um_facet` and retention floors (`E-020`)

- **Txn:** `ADD COLUMN um_facet text`; index; `ADD CONSTRAINT … CHECK (kind NOT IN ('claim','abstraction') OR retention = 'durable') NOT VALID`, then `VALIDATE`; an enum-style `CHECK` on the six facet values.
- **Fail (analysis):** requires `M1` first (the check reads `kind` and `retention`). v1 backfill sets every row `durable`, so validation passes on existing data. The check must be in place **before** any W2 working-memory feature, or a `working` claim becomes representable.
- **Rollback:**
  ```sql
  ALTER TABLE memory_version DROP CONSTRAINT memory_version_retention_floor, DROP CONSTRAINT memory_version_um_facet_check;
  DROP INDEX IF EXISTS memory_version_um_facet_idx;
  ALTER TABLE memory_version DROP COLUMN um_facet;
  DELETE FROM _cme_schema_migrations WHERE version = <M8 version>;
  ```
- **Verify:** `tests/integration/test_retention_floors.py`: a `working` claim is rejected; purging a working episode leaves its durable claims answerable.
- **Online?** Yes (`NOT VALID` then `VALIDATE`).

### M9 — path `CHECK`, `path_with_prefix`, prefix index (`E-011`)

- **Pre:** `M2`.
- **Txn:** `CHECK (path LIKE '/%' AND path LIKE '%/')`; generated `path_with_prefix`; `CREATE INDEX … (path_with_prefix text_pattern_ops)` (X7: outside the transaction, `CONCURRENTLY` available).
- **Fail (measured, PostgreSQL 16.15):**
  - `path LIKE '/u/4%'` returns `/u/4/` **and `/u/42/`** (the original leak).
  - With the trailing separator, `LIKE '/u/4/%'` is correct for that case, **but `LIKE '/u/a_b/%'` also returns `/u/aXb/`**, because `_` is a wildcard.
  - `starts_with(path_with_prefix, '/u/a_b/')` and `path ^@ '/u/a_b/'` return only `/u/a_b/`, and `starts_with` still uses the `text_pattern_ops` index (`Index Cond: path_with_prefix ~>=~ '/u/4/' AND path_with_prefix ~<~ '/u/40'`).
  - Therefore the policy and the engine use `starts_with`, not `LIKE`: **decided, `V2-D35`** (r7; `scope.paths.subtree_predicate`).
- **Rollback (the index outside the transaction, as it was built):**
  ```sql
  DROP INDEX CONCURRENTLY IF EXISTS <t>_path_with_prefix_idx;     -- per table
  ALTER TABLE <t> DROP CONSTRAINT <t>_scope_path_shape, DROP COLUMN path_with_prefix;
  ```
  (part of M2's rollback when they are applied together)
- **Verify:** `tests/integration/test_scope_isolation.py::test_wildcard_characters_in_ids_do_not_widen_a_selector` and the `/u/4/` vs `/u/42/` test.
- **Online?** Yes for the index (`CONCURRENTLY`); the `CHECK` is validated with `NOT VALID` then `VALIDATE`.

## 3. Findings that need an owner

### 3.1 Ownership (updated r8)

Every gap this review originally found unowned now has an owner or a recorded deferral:

| Gap | Owner / disposition |
|---|---|
| Multi-version runner (X1/X2) | Done in r7 (`29e63a3`); each W1 task extends the registry (D1) |
| `M5` | Deferred past W1 (`V2-D43`, D4) |
| `M7` | `E-010` (`V2-D38`, `V2-D43`, D3) |
| Staging-row mapping (`M1`) | Decided (`V2-D38`, D2) |
| `observed_at` backfill (`M3`) | Proposed D8, owner `E-012`; blocks `E-012` until approved |
| `layer` many-to-one (`M1`) | Proposed D6, owner `E-010`; blocks `E-010` until approved |
| `persona` ledger | Proposed D7, owners `E-010`/`E-014` |
| Generated `user_id` | Proposed D11 (do not generate), owner `E-011` |

### 3.2 Online vs offline summary

| Migration | Online? | Notes |
|---|---|---|
| M1 | Yes | batched backfill, `NOT VALID` then `VALIDATE` |
| M2 | **No** | table rewrite, rebuild of all `user_id` dependents |
| M3 | **Rename in place: yes; as specified (recreate): no** | rename-only 3.2 ms at 100k and 400k rows; drop+rename+recreate 0.46 s / 7.4 s / 48 s at 10k / 100k / 400k (measured) |
| M4 | Mostly | short lock for the key rebuild |
| M5 | Yes | one-way once a `NULL` row exists |
| M6 | Yes | new objects |
| M7 | Yes | no structural change |
| M8 | Yes | `NOT VALID` then `VALIDATE` |
| M9 | Yes | index `CONCURRENTLY`; outside the transaction |

## 4. How the measurements were taken

All against the disposable `pgvector/pgvector:0.8.7-pg16-bookworm` container (PostgreSQL 16.15), in a throw-away schema
created from `schema/001_full_schema.sql`, with the scripts kept out of the repository.

| Claim | Method |
|---|---|
| Three functions reference `valid_range` | `pg_proc.prosrc LIKE '%valid_range%'` in the schema |
| Rollback restores column and constraint | run the M3 steps in `conn.transaction(force_rollback=True)`; check `information_schema.columns` and `pg_get_constraintdef` |
| Stale `guard_update` fails | commit the rename without step 5; insert a row; `UPDATE … SET reinforcement_count = reinforcement_count + 1` |
| Constraint identical modulo rename | `pg_get_constraintdef(oid)` before, after `replace('valid_range','event_range')` equals after |
| 43 assertions after the rename | `bitemporal_on_full.sql` and `validation/bitemporal_checks.sql` with `valid_range` → `event_range` substituted; count of `PASS` rows |
| Rename-only vs recreate | same populated tables; time `ALTER TABLE … RENAME COLUMN` alone and read back `pg_get_constraintdef` |
| Timings | insert N active rows with non-overlapping subjects (triggers and RLS disabled for the insert only), then time the transaction in step 1-3 |
| `LIKE` vs `starts_with` | the SQL in section 2, `M9`, run in a scratch schema with `EXPLAIN (COSTS OFF)` |
| `user_id` dependency surface (M2 coverage, r8) | full r8 schema applied with the runner into a scratch schema; `pg_constraint` / `pg_policy` / `pg_indexes` queried for every object on `user_id` |
| Generated `user_id` compatibility (M2, r8) | one transaction, always rolled back: `DROP COLUMN user_id` without `CASCADE` (refused), then `CASCADE`, re-add as `GENERATED ALWAYS AS (split_part(scope_path, '/', 3)) STORED`, recreate the FK, `job_extraction_idempotent`, the RLS policy, `no_bitemporal_overlap` and the temporal index; then a v1-style `INSERT` naming `user_id`, a V2-style `INSERT` naming only `scope_path`, and an `INSERT` for an unknown user |
| Stored `user_id` + consistency `CHECK` (D11, r8) | rolled-back transaction: `ADD COLUMN scope_path`, `CHECK (… = '/u/' \|\| user_id \|\| '/') NOT VALID` + `VALIDATE`; v1 insert without `scope_path`, consistent and inconsistent dual-writes |

Timings are from a single containerised development database and show scaling, not production capacity.
