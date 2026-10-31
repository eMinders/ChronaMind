# Migration Risk Review (M1–M9)

**Task:** `MA-016` · **Status:** design review; no migration is implemented yet. **Governs:** `E-010`,
`E-011`, `E-012`, `E-013`, `E-014`, `E-020`. **References:** `ARCHITECTURE_V2.md` section 12 (the migration
table), section 3 (clocks), section 5 (scope), `INVARIANT_LEDGER.md`, `THREAT_MODEL.md`.

For each of the nine migrations this document gives: preconditions, the statement list (one transaction),
the failure modes, the rollback, the verification command, and whether it can run online. Claims marked
**measured** come from a dry run on a scratch schema in the disposable PostgreSQL 16.15 / pgvector 0.8.7
database (commands are in section 4). Everything else is analysis and is labelled as such.

## 1. Cross-cutting facts that change every migration

| # | Fact | Consequence |
|---|---|---|
| X1 | `schema/migrate.py` is bound to **one** version: `VERSION = 1`, one source checksum, one catalog fingerprint; unknown versions and edited historical sources are refused (`REFACTORING.md` section 2.3). | `M1`-`M9` cannot be applied as they stand. The runner needs an ordered registry (version, filename, checksum, expected catalog digest, down script). **No task owns this** (see 3.1). |
| X2 | The catalog fingerprint covers columns, constraints, triggers, functions and policies. | Every migration changes it. `status` reports `drifted` until the registry records the new digest. |
| X3 | PL/pgSQL bodies are **late-bound text**. `guard_update`, `enforce_archival_invariant` and `guard_derivation_coverage` reference `valid_range`; `guard_update` also compares `layer`, `user_id`, `valid_from_basis`. | A rename or column swap does not update them and fails only when the trigger fires. **Measured:** forgetting to recreate `guard_update` after the M3 rename gives `UndefinedColumn: record "new" has no field "valid_range"` on the first update. |
| X4 | `guard_update` compares a fixed tuple of 14 columns and rejects any change. | A backfill `UPDATE` of **new** columns passes. Any change to a guarded column (rename, type, generated) needs the function recreated in the same transaction. A new column that must be immutable has to be **added to that tuple** or it silently becomes mutable. |
| X5 | Engine SQL is plain text. `valid_range` appears on 54 lines across `engine/transactions.py` (41), `render.py` (6), `predicates.py` (4), `gate.py`, `retrieval.py`, `consolidator.py`, plus 9 test files, `scripts/bench_retrieval.py` and `schema/bitemporal_on_full.sql`. | A column rename is a code change as well as a DDL change; both must land in one PR or the Tier-A wall goes red. |
| X6 | `provision_runtime_role` grants on a hard-coded list of 11 tables (`security/provisioning.py`, `RL-071`). | New tables from `M6` must be added to the grants and to the forced-RLS verification, or the restricted role cannot use them. |
| X7 | `CREATE INDEX CONCURRENTLY` cannot run inside a transaction; `EXCLUDE` constraints cannot be built concurrently. | "Single transaction" and "online" conflict for index-heavy steps; the table below says which. |

## 2. Per-migration review

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
- **Rollback:** `DROP COLUMN` the three columns; safe while dual-write is on and `layer` is still stored. After `layer` is dropped it is **not** reversible without the discriminator in finding 1.
- **Verify:** `tests/integration/test_migration_m1.py` (from `E-010`); `SELECT count(*) FROM memory_version WHERE kind IS NULL OR lifecycle IS NULL OR retention IS NULL` returns `0`; the Tier-A wall stays green.
- **Online?** Yes for steps 1-3 (batched, `NOT VALID` then `VALIDATE`); the `guard_update` swap is a brief lock.

### M2 — scope hierarchy (`E-011`, with `M9`)

- **Pre:** `MA-011` decisions recorded (selector semantics; the `LIKE` finding in `THREAT_MODEL.md` T-02); every existing `app_user.id` checked: `SELECT id FROM app_user WHERE id LIKE '%/%'` must return nothing (a `/` in an id would forge a path, T-03).
- **Txn:** (1) add `path text`, `visibility text`; (2) backfill `path = '/u/' || user_id || '/'`, `visibility = 'private'`; (3) `CHECK (path LIKE '/%' AND path LIKE '%/')`; (4) add the generated `user_id` and `path_with_prefix`; (5) drop and recreate every dependent of `user_id` (11 RLS policies, `no_bitemporal_overlap`, `memory_version_temporal_idx`, the foreign key, `guard_update`); (6) set the `app.read_scope_prefixes` policy.
- **Fail (analysis):**
  1. PostgreSQL 16 cannot turn an existing column into a generated one. `user_id` must be **replaced** (new generated column, drop the old, rename), which rewrites `memory_version` and forces every dependent in step 5 to be rebuilt.
  2. **Measured:** a stored generated `user_id` derived from the path **can** carry a foreign key to `app_user`, so referential integrity survives.
  3. Policies on all 11 user tables reference `user_id`; changing only `memory_version` leaves ten tables on the old predicate.
  4. Backfill collisions are impossible when ids contain no `/`; ids that do would silently create paths under another tenant's subtree.
- **Rollback:** reverse the column swap and drop `path`/`visibility`; paths are derivable from `user_id`, so no data is lost.
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
- **Rollback (measured):** running steps 1-3 inside a transaction that is rolled back restores the original column name and an identical constraint. After commit, rollback is the inverse transaction (rename back and rebuild again). The architecture's "reversible via view" means a `memory_version_v1` compatibility view; a view cannot reuse the table's own name.
- **Verify (measured):** with `valid_range` replaced by `event_range` throughout, `validation/bitemporal_checks.sql` produces the same **43** `PASS` rows and `ALL ASSERTIONS PASSED` as the original, and `schema/bitemporal_on_full.sql` (the full-table replay) also produces **43** `PASS` rows. The reduced script builds its own table, so replaying it proves the constraint definition and semantics; the full-table replay proves the real table. Both are required by the acceptance criteria and neither alone is enough.
- **Online?** Depends on the decision above. **Rename in place: effectively yes** (a millisecond-scale `ACCESS EXCLUSIVE`, but code and database must change together, since the engine's SQL names the column, X5). **Drop-and-recreate as specified: no.** It holds `ACCESS EXCLUSIVE` on `memory_version` while rebuilding a GiST index; **measured 0.46 s at 10,000 rows, 7.4 s at 100,000, 48 s at 400,000** (containerised development database). It blocks reads and writes for that time, so schedule it as a maintenance window and size the table first.

### M4 — generalised `SOURCE` (`E-013`)

- **Pre:** `E-010`. Inventory every join on `memory_evidence.turn_id` (`deletion.py`, queue purge by `turn_id`, `enforce_provenance`).
- **Txn:** add `source_type`, `source_ref`, `span`, `content_hash`; backfill `('turn', turn_id, evidence_span)` with `content_hash = sha256(turn.content)`; keep `turn_id` and its foreign key until sunset; rebuild the primary key on the new tuple; recreate `enforce_provenance` if its body changes.
- **Fail (analysis):** replacing `turn_id` removes the foreign key that today prevents dangling evidence, and one column cannot be a foreign key for only some rows. Keep a nullable `turn_id` with the key and a `CHECK (source_type <> 'turn' OR turn_id IS NOT NULL)`. `type='external'` content lies **outside** the deletion boundary and the closure cannot remove it (must be documented, as the acceptance criteria require). The queue's "purge jobs for deleted turns" logic must learn `source_ref`.
- **Rollback:** drop the new columns; originals untouched while `turn_id` is retained.
- **Verify:** `tests/integration/test_migration_m4.py`; evidence rows byte-identical (`md5` over the old columns before and after); a tool-call episode round-trips with provenance.
- **Online?** Mostly: columns and backfill in batches; the primary-key rebuild needs a short lock (build the unique index `CONCURRENTLY` first, outside the transaction, per X7).

### M5 — optional `predicate_schema`

- **No owning task.** `E-010`-`E-020` never mention it (see 3.1).
- **Txn:** `ALTER TABLE memory_version ALTER COLUMN predicate_key DROP NOT NULL`; the foreign key stays.
- **Fail (analysis):** `no_bitemporal_overlap` lists `predicate_key WITH =`. `NULL = NULL` is not true, so **rows with no predicate never conflict** and single-valued exclusivity is silently off for them. That is the stated intent (the conflict detector becomes the arbiter) but it weakens `I5` and must be an explicit decision, with the detector's error rate measured, not assumed.
- **Rollback:** `SET NOT NULL` fails once any `NULL` row exists, so this migration is **effectively one-way** after the first predicate-less row.
- **Verify:** a test that two predicate-less rows with overlapping ranges are accepted (documenting the weakened guarantee) and that rows with predicates still conflict.
- **Online?** Yes (metadata-only).

### M6 — structural tables (`E-014`)

- **Txn:** `CREATE TABLE ASSOCIATION, ENTITY, INFERENCE_METHOD, ABSTRACTION_STATS, VERIFICATION, NEGATIVE_CONSTRAINT`; `ENABLE` and `FORCE ROW LEVEL SECURITY` plus an isolation policy on each; an append-only trigger on `INFERENCE_METHOD`; **no** cascade from `ASSOCIATION` to associated memories.
- **Fail (analysis):** a table created without `FORCE ROW LEVEL SECURITY` is a cross-tenant read (X6, `INVARIANT_LEDGER.md` `I7`). `ENTITY` ids must be opaque or the existence oracle in `THREAT_MODEL.md` T-07 appears. Add each table to `USER_TABLES` in `security/provisioning.py` so the runtime role and `verify_forced_rls` cover it.
- **Rollback:** `DROP TABLE` (new tables only).
- **Verify:** `tests/integration/test_migration_m6.py`; deleting a memory removes its associations without deleting associated memories; `verify_forced_rls` passes for the new tables.
- **Online?** Yes (new objects).

### M7 — `archived` kept as a retention value (no owner)

- **No owning task** (see 3.1); the natural home is `E-016` (lifecycle state machine), which defines `retired`.
- **Fail (analysis):** two meanings coexist: v1 `status='archived'` with `archival_branch` and the `(S) ∨ (C)` trigger, and V2 `retention` + `retired`. The `(S) ∨ (C)` guarantee (Invariant 10) must keep holding for the v1 value while new code stops using it, or answerability can be lost silently.
- **Rollback:** nothing structural to undo; revert the code that stops writing `archived`.
- **Verify:** the existing `test_r7_end_multivalued_and_r5_invariant_10_d27_archival` and `test_archival_guard_verification_fails_closed_when_triggers_are_absent` stay green.
- **Online?** Yes.

### M8 — `um_facet` and retention floors (`E-020`)

- **Txn:** `ADD COLUMN um_facet text`; index; `ADD CONSTRAINT … CHECK (kind NOT IN ('claim','abstraction') OR retention = 'durable') NOT VALID`, then `VALIDATE`; an enum-style `CHECK` on the six facet values.
- **Fail (analysis):** requires `M1` first (the check reads `kind` and `retention`). v1 backfill sets every row `durable`, so validation passes on existing data. The check must be in place **before** any W2 working-memory feature, or a `working` claim becomes representable.
- **Rollback:** drop the constraint and column.
- **Verify:** `tests/integration/test_retention_floors.py`: a `working` claim is rejected; purging a working episode leaves its durable claims answerable.
- **Online?** Yes (`NOT VALID` then `VALIDATE`).

### M9 — path `CHECK`, `path_with_prefix`, prefix index (`E-011`)

- **Pre:** `M2`.
- **Txn:** `CHECK (path LIKE '/%' AND path LIKE '%/')`; generated `path_with_prefix`; `CREATE INDEX … (path_with_prefix text_pattern_ops)` (X7: outside the transaction, `CONCURRENTLY` available).
- **Fail (measured, PostgreSQL 16.15):**
  - `path LIKE '/u/4%'` returns `/u/4/` **and `/u/42/`** (the original leak).
  - With the trailing separator, `LIKE '/u/4/%'` is correct for that case, **but `LIKE '/u/a_b/%'` also returns `/u/aXb/`**, because `_` is a wildcard.
  - `starts_with(path_with_prefix, '/u/a_b/')` and `path ^@ '/u/a_b/'` return only `/u/a_b/`, and `starts_with` still uses the `text_pattern_ops` index (`Index Cond: path_with_prefix ~>=~ '/u/4/' AND path_with_prefix ~<~ '/u/40'`).
  - Therefore the policy and the engine should use `starts_with`, not `LIKE`. This updates the "`path_with_prefix LIKE ANY(...)`" wording in `ARCHITECTURE_V2.md` 5 and needs a decision record (`THREAT_MODEL.md` 3.1).
- **Rollback:** drop the index, generated column and constraint.
- **Verify:** `tests/integration/test_scope_isolation.py::test_wildcard_characters_in_ids_do_not_widen_a_selector` and the `/u/4/` vs `/u/42/` test.
- **Online?** Yes for the index (`CONCURRENTLY`); the `CHECK` is validated with `NOT VALID` then `VALIDATE`.

## 3. Findings that need an owner

### 3.1 Work with no owning task

| Gap | Why it matters | Suggested home |
|---|---|---|
| Migration runner multi-version support (X1/X2) | Nothing in `M1`-`M9` can be applied without it | Fold into `E-010` or add a small task before it |
| `M5` (optional `predicate_key`) | Weakens `I5`; one-way | Decide whether `M5` is in scope for W1; if so, assign it |
| `M7` (`archived` as retention) | Dual meaning; protects Invariant 10 | `E-016` |
| `observed_at` backfill rule and `status` → `lifecycle` mapping for staging rows (`M1`, `M3`) | Backfills cannot be written without them | Decision records before `E-010`/`E-012` |
| `layer` many-to-one mapping (`M1`) | Acceptance criteria are mutually inconsistent | Decision record before `E-010` |

`MA-017` (the architecture-to-plan coverage matrix) should flag `M5` and `M7` as unowned.

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

Timings are from a single containerised development database and show scaling, not production capacity.
