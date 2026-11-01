# Security and Correctness Review (r10)

Companion to `PRE_W1_INDEPENDENT_AUDIT.md`: for each fix, the attack or failure, the mechanism, and the test that
proves it, together with the **control** showing the test fails without the fix. AI-assisted review; see
`PROJECT_OWNER_REVIEW_SUMMARY.md`.

## 1. Tenant integrity (migration 4, `V2-D54`, PR #105)

| Property | Mechanism | Test | Control (fails without fix) |
|---|---|---|---|
| Another tenant's row cannot be referenced; the refusal is identical to a missing row | composite `(id, user_id)` foreign keys | `test_tenant_integrity_db.py::test_another_tenants_turn_is_indistinguishable_from_a_missing_one` | `test_control_before_migration_4_the_reference_is_accepted_and_is_an_oracle` |
| No tenant can block another's deletion | same | `::test_another_tenant_cannot_block_a_users_deletion` | `::test_control_before_migration_4_the_deletion_is_blocked` |
| Temporary objects cannot shadow engine tables in triggers | `search_path = <schema>, pg_catalog, pg_temp` on every function | `::test_a_temporary_table_cannot_shadow_an_engine_table_inside_a_trigger` | `::test_control_before_migration_4_shadowing_bypasses_the_provenance_invariant` |
| Audit and deletion receipts are append-only for the runtime | exact `TABLE_GRANTS` | `::test_audit_and_deletion_receipts_cannot_be_rewritten_by_the_runtime`; `test_security_scoping.py::test_rls_sql_grants_equal_the_least_privilege_matrix_exactly` | the r9 grants allowed both |
| Future migrations keep both properties | catalog rules | `::test_rule_every_foreign_key_between_tenant_tables_carries_user_id`, `::test_rule_every_engine_function_pins_search_path_with_pg_temp_last` | both fail on the pre-migration-4 catalog (mutation run, r10) |
| Upgrade path | runner | `test_migrations.py::test_a_version_one_schema_is_pending_and_apply_advances_it_in_order` (a real migration-1 install upgraded through 4, data retained) | |

## 2. Invariant 8: content-free audit and logs (`RL-072`, `V2-D55`, PR #109)

| Property | Mechanism | Test | Control |
|---|---|---|---|
| Every audit payload is allowlisted | `audit_guard.content_free_details` on all five writers | `test_audit_guard.py::test_every_jsonb_audit_payload_in_the_engine_passes_the_guard`, `::test_every_literal_audit_dict_uses_only_allowed_keys` | restoring the `memory_key` field fails the second (mutation run) |
| Prose and content keys cannot pass | identifier-shaped values, `CONTENT_KEYS` ∩ allowlist = ∅ | `::test_values_that_could_carry_content_are_rejected`, `::test_content_keys_can_never_be_audit_keys` | |
| No log call carries content keys | static rule; `redact_log_event` processor | `::test_engine_log_calls_never_pass_content_keys` | failed on the consolidator's `memory_key` before the fix |
| Database backstop is real, and only a backstop | `audit_details_content_free` | `test_audit_content_db.py::test_backstop_check_rejects_each_forbidden_top_level_key`, `::test_backstop_check_is_only_a_blacklist_which_is_why_the_guard_exists` | |
| Channel 1 sees audit rows | scanner reads `audit_event` | `::test_channel_1_counts_a_canary_left_in_audit_details_or_memory_ref` | |
| Channel 1 always scans storage | caller surfaces supplement, never replace | `test_leak_scanner_db.py::test_a_cache_probe_supplements_and_never_replaces_the_storage_scan` | failed before the scanner fix |

## 3. Invariant 10: archival and deletion (`RL-090`, `V2-D55`)

`tests/integration/archival/test_t1_to_t5.py`, one test per contract row of `TECHNICAL_SPECIFICATION.md` §6.
`T5` measures all seven channels against the engine's own retrieval (dense + lexical), rendered context, an
extractive answer and the live structlog capture of the whole scenario; a pre-deletion run of the same
measurement must see the canary (otherwise a clean verdict would be vacuous). **Control:** with the deletion's
trigger deferral removed, `T5` fails with `archival_breaks_covered_source_invariant` (mutation run, r10).
`test_t5_counterpart_deleting_only_the_replacement_is_still_refused` proves the deferral does not weaken
Invariant 10.

## 4. Invariant 9: replay (`RL-092`, `V2-D55`)

`tests/integration/test_replay_gate_db.py`: an out-of-order session (with a tie and a backwards step) is extracted
through the real queue and engine, then replayed twice (same jobs; then new job ids with the same extraction
identity). Zero duplicate versions; recorded instants strictly increasing and equal to the allocated ticks.
`test_every_clause_is_covered_by_a_test_that_exists` pins each `RL-092` clause to its proving test.

## 5. Other correctness fixes

| Fix | Test |
|---|---|
| Gate: `0.0` per-predicate threshold honoured (#110) | `test_gate.py::test_a_zero_predicate_threshold_is_not_overridden_by_the_global_one` (fails on the old code) |
| CI keeps every gate, pinned actions and images, read-only token, linted workflows | `tests/test_ci_workflow.py` (removing a gate fails it, mutation run) |
| Integration suites and Suite D build the shipped schema | `tests/integration/schema.py`, `migrate.migration_scripts`; recorded Suite D reports re-run byte-identically |

## 6. Residual risks (owned, none blocks W1)

| ID | Risk | Owner |
|---|---|---|
| T-27 / S-04 | caller-chosen global keys (v1 library API) | `E-039` |
| T-28 / S-05 | `app.clock` backdating by a compromised runtime | `E-012` |
| T-29 / S-06 | plaintext `target_ref` in deletion receipts (tenant-internal) | `E-037` |
| T-30 / S-07 | grant principal existence | `E-039` |
| S-08 | exclusive-evidence deletion offer not implemented in v1 | `E-037` |
| — | SQL timing side channels, WAL/backups (accepted, `THREAT_MODEL.md` §4) | — |
