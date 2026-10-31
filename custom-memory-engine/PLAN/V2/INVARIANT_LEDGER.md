# Invariant Ledger

Every invariant the project **declares** must appear here with an honest enforcement status.
`tests/test_invariants_declared_and_enforced.py` parses the declarations from their source
documents (`src/custom_memory_engine/schema/README.md` for v1 `Invariant 1`-`10`;
`ARCHITECTURE_V2.md` section 11 for V2 `I1′`-`I17`) and fails if a declared invariant has no row,
if an `ENFORCED` row cites a site or test that does not exist, or if an unenforced row does not say
what is missing and which task owns it. Task `E-004`.

**Statuses**

| Status | Meaning | Requires |
|---|---|---|
| `ENFORCED` | A mechanism exists and a named test exercises it | resolvable site and test |
| `PARTIAL` | Part of the invariant is enforced and tested; the remainder is not | site, test, gap, owner |
| `UNENFORCED` | Declared, but no sufficient mechanism or test exists today | gap, owner |
| `PLANNED` | V2 invariant whose mechanism belongs to a task not yet built | gap, owner |

Citation syntax: `path::symbol`. For a site the symbol must occur in the file; for a test it must be
a test function or method defined in the file. A row may split an invariant (`Invariant 7 / runtime
role`) so a gap in one half is not hidden by the other.

## v1 invariants (frozen baseline)

| Invariant | Status | Enforcement site | Tests | Gap | Owner |
|---|---|---|---|---|---|
| Invariant 1 | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::trg_memory_version_provenance` | `tests/integration/test_full_schema.py::test_invariant_1_rejects_active_row_without_provenance`, `tests/integration/test_full_schema.py::test_invariant_1_accepts_evidence_or_derivation` | | |
| Invariant 2 | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::recorded_iff_believed` | `tests/integration/test_full_schema.py::test_forty_three_bitemporal_assertions_pass_against_full_memory_version` | | |
| Invariant 3 | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::guard_update`, `src/custom_memory_engine/clock/allocation.py::allocate_clock` | `tests/integration/test_full_schema.py::test_guard_update_rejects_content_edit_and_allows_archival_branch`, `tests/integration/test_clock_queue_db.py::test_live_clock_uses_transaction_timestamp_and_allocates_only_once` | | |
| Invariant 4 | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::no_bitemporal_overlap` | `tests/integration/test_full_schema.py::test_forty_three_bitemporal_assertions_pass_against_full_memory_version`, `tests/integration/test_clock_queue_db.py::test_clock_serializes_same_user_while_other_user_progresses` | | |
| Invariant 5 | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::guard_update`, `src/custom_memory_engine/engine/transactions.py::supersede_single` | `tests/integration/test_transactions_db.py::test_e1_e3_supersession_and_boundary_revision_lifecycle`, `tests/integration/test_full_schema.py::test_guard_update_rejects_content_edit_and_allows_archival_branch` | | |
| Invariant 6 | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::recorded_iff_believed`, `src/custom_memory_engine/engine/predicates.py::audit_as_of` | `tests/integration/test_predicates_db.py::test_compiled_predicates_and_executor_enforce_real_unprivileged_rls` | | |
| Invariant 7 / policies | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::FORCE ROW LEVEL SECURITY` | `tests/integration/test_full_schema.py::test_row_level_security_is_forced_on_user_data_tables`, `tests/integration/test_transactions_db.py::test_non_superuser_rls_tenant_isolation_for_transactions` | | |
| Invariant 7 / runtime role | `UNENFORCED` | | | No restricted runtime role, grants or tenant-scoping guard exist: `src/custom_memory_engine/security/` is absent, so the policies above are only exercised by test fixtures, never on a production path. | `RL-071`, `MA-011`, `E-011` |
| Invariant 8 / deletion | `ENFORCED` | `src/custom_memory_engine/engine/deletion.py::delete_memory` | `tests/integration/test_retrieval_deletion_db.py::test_delete_memory_removes_key_and_cascades_without_touching_other_keys`, `tests/integration/test_retrieval_deletion_db.py::test_deleted_content_is_not_recoverable_by_retrieval` | | |
| Invariant 8 / seven-channel scanner | `PARTIAL` | `src/custom_memory_engine/deletion/scanner.py::scan_deleted_key` | `tests/test_deletion_modules.py::test_scanner_reports_clean_when_all_seven_channels_measure_zero` | Database probes exist for channels 1, 2 and 4 only; channels 3 and 5-7 are caller-supplied and there is no harness-side `leak_scanner.py`. | `RL-070`, `E-037` |
| Invariant 8 / content-free audit | `UNENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::audit_details_content_free` | | The DDL `CHECK` has no test, and no audit or log redaction guard exists. | `RL-072` |
| Invariant 9 | `PARTIAL` | `src/custom_memory_engine/schema/001_full_schema.sql::job_extraction_idempotent`, `src/custom_memory_engine/queue/worker.py::process_one` | `tests/integration/test_full_schema.py::test_job_idempotency_unique_constraints`, `tests/integration/test_clock_queue_db.py::test_enqueue_deduplicates_both_durable_keys_without_resetting_completed_work` | No end-to-end gate proving that a replayed job yields zero duplicate memory versions. | `RL-092` |
| Invariant 10 | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::enforce_archival_invariant`, `src/custom_memory_engine/engine/transactions.py::archive_version` | `tests/integration/test_transactions_db.py::test_r7_end_multivalued_and_r5_invariant_10_d27_archival`, `tests/integration/test_transactions_db.py::test_archival_guard_verification_fails_closed_when_triggers_are_absent` | | |
| Invariant 10 / T1-T5 harness | `UNENFORCED` | | | The standalone `T1`-`T5` archival regression harness does not exist; `T1`-`T4` live inline in `test_transactions_db.py` and `T5` is absent. | `RL-090` |

## V2 invariants (target architecture)

None of the V2 mechanisms exist yet; each row names the task that will create it. A row moves to
`ENFORCED` only in the same PR that adds the mechanism and its test.

| Invariant | Status | Enforcement site | Tests | Gap | Owner |
|---|---|---|---|---|---|
| I1′ | `PLANNED` | | | Grounded-or-hypothesis rendering depends on the channel contract and lifecycle `CHECK`. | `E-016`, `E-017`, `E-035` |
| I2 | `PLANNED` | | | Generalised provenance needs `SOURCE`; v1 `Invariant 1` covers turn evidence only. | `E-013` |
| I3 | `PLANNED` | | | The immutable method registry does not exist. | `E-014` |
| I4 | `PLANNED` | | | The three-clock model needs `observed_at` and the `event_range` rename. | `E-012` |
| I5 | `PLANNED` | | | Scope-level exclusivity depends on the scope hierarchy; v1 `Invariant 4` covers the per-user case. | `E-011`, `E-012` |
| I6 | `PLANNED` | | | Retention orthogonality needs the retention axis and its reachability property test. | `E-010`, `E-018`, `E-020` |
| I7 | `PLANNED` | | | Path-based scope isolation; today only per-user RLS exists (see v1 `Invariant 7`). | `E-011`, `MA-011` |
| I8 | `PLANNED` | | | Unbounded closure over the provenance graph; v1 bounds the walk at depth 3. | `E-037` |
| I9 | `PLANNED` | | | Per-scope serialisation key. | `E-011` |
| I10 | `PLANNED` | | | Generalisation to `retired` and abstraction coverage. | `E-016`, `E-037` |
| I11 | `PLANNED` | | | Verification records and the disjointness check do not exist. | `C-010`, `C-021` |
| I12 | `PARTIAL` | `tests/test_boundaries.py::FORBIDDEN_MODULES` | `tests/test_boundaries.py::test_no_product_or_baseline_imports` | The boundary test forbids application, agent-framework and baseline modules but not model-provider SDKs. | `E-025` |
| I13 | `PLANNED` | | | Needs deletion ordering and closure-hash staleness. | `E-037`, `E-038` |
| I14 | `PLANNED` | | | Needs channel assembly and the fuzz suite. | `E-017`, `E-035` |
| I15 | `PLANNED` | | | Needs the opportunity ledger on the abstraction artifact. | `C-010`, `C-021` |
| I16 | `PLANNED` | | | Needs `NEGATIVE_CONSTRAINT` and the sticky-dissent path. | `E-014`, `UM-012` |
| I17 | `PLANNED` | | | Needs opaque cross-scope provenance and entity opacity. | `E-011`, `MA-011` |
