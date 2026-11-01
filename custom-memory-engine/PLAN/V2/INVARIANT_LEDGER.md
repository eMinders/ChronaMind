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

**E-004 acceptance criterion 3 (#52) is superseded.** The issue was written when v1 `Invariant 7` and
`Invariant 8` had known gaps (#47, #46) and required that they *not* be `ENFORCED`. Both gaps were closed on
`main` in r7 (#47: `security/provisioning.py`, restricted runtime role, `tests/integration/test_rls_isolation.py`;
#46: channels 3, 5-7 of the leak scanner, `tests/integration/test_leak_scanner_db.py`), so the rows for those
halves are `ENFORCED`, and `test_gaps_closed_in_r7_are_enforced_with_tests` pins it. The criterion's purpose,
that a known gap is reported and never silently passed, still holds for the remaining halves (`Invariant 8 /
content-free audit`, `Invariant 9`, `Invariant 10`; `test_known_v1_gaps_are_reported_not_silently_passed`).
Superseded by Project Owner decision, r9 (`APPROVAL.md` Amendment 2).

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
| Invariant 7 / runtime role | `ENFORCED` | `src/custom_memory_engine/security/rls.sql::NOBYPASSRLS`, `src/custom_memory_engine/security/provisioning.py::verify_runtime_role`, `src/custom_memory_engine/security/provisioning.py::privilege_violations` | `tests/integration/test_rls_isolation.py::test_provisioned_role_is_restricted_and_every_user_table_forces_rls`, `tests/integration/test_rls_isolation.py::test_cross_user_update_and_delete_touch_zero_rows_and_insert_is_rejected`, `tests/integration/test_rls_isolation.py::test_suite_i_dev_probes_report_zero_cross_user_exposure_under_the_restricted_role` | | |
| Invariant 8 / deletion | `ENFORCED` | `src/custom_memory_engine/engine/deletion.py::delete_memory` | `tests/integration/test_retrieval_deletion_db.py::test_delete_memory_removes_key_and_cascades_without_touching_other_keys`, `tests/integration/test_retrieval_deletion_db.py::test_deleted_content_is_not_recoverable_by_retrieval` | | |
| Invariant 8 / scanner channels 1, 2, 4 | `ENFORCED` | `src/custom_memory_engine/deletion/scanner.py::database_channel_probes` | `tests/test_deletion_modules.py::test_scanner_reports_leftovers_in_each_channel` | | |
| Invariant 8 / scanner channels 3, 5-7 | `ENFORCED` | `evaluation/harness/leak_scanner.py::harness_probes`, `evaluation/harness/leak_scanner.py::verdict` | `tests/integration/test_leak_scanner_db.py::test_normal_deletion_measures_zero_on_all_seven_channels`, `evaluation/harness/tests/test_leak_scanner.py::test_verdict_fails_closed_when_channels_are_unmeasured`, `evaluation/harness/tests/test_leak_scanner.py::test_a8_detection_trips_on_an_injected_leak` | | |
| Invariant 8 / content-free audit | `UNENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::audit_details_content_free` | | The DDL `CHECK` has no test, and no audit or log redaction guard exists. | `RL-072` |
| Invariant 9 / job identity | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::job_extraction_idempotent`, `src/custom_memory_engine/queue/worker.py::process_one` | `tests/integration/test_full_schema.py::test_job_idempotency_unique_constraints`, `tests/integration/test_clock_queue_db.py::test_enqueue_deduplicates_both_durable_keys_without_resetting_completed_work` | | |
| Invariant 9 / end-to-end replay | `UNENFORCED` | | | No gate proves that a replayed job yields zero duplicate memory versions end to end; coverage is partial (the wider `RL-092` gate is pending). | `RL-092` |
| Invariant 10 | `ENFORCED` | `src/custom_memory_engine/schema/001_full_schema.sql::enforce_archival_invariant`, `src/custom_memory_engine/engine/transactions.py::archive_version` | `tests/integration/test_transactions_db.py::test_r7_end_multivalued_and_r5_invariant_10_d27_archival`, `tests/integration/test_transactions_db.py::test_archival_guard_verification_fails_closed_when_triggers_are_absent` | | |
| Invariant 10 / T1-T5 harness | `UNENFORCED` | | | The standalone `T1`-`T5` archival regression harness does not exist; `T1`-`T4` live inline in `test_transactions_db.py` and `T5` is absent. | `RL-090` |

## V2 invariants (target architecture)

Most V2 mechanisms do not exist yet; each such row names the task that will create it. The r7 blocker
resolutions (`DECISIONS_V2.md` §5) added tested **primitives** for some invariants; those rows are `PARTIAL`:
the primitive and its test exist, but it is not yet wired into the engine tables and flows, which is W1 work
owned by the named task. A row moves to `ENFORCED` only in the same PR that adds the integrated mechanism and
its test.

| Invariant | Status | Enforcement site | Tests | Gap | Owner |
|---|---|---|---|---|---|
| I1′ | `PARTIAL` | `src/custom_memory_engine/model/axes.py::TRANSITIONS`, `src/custom_memory_engine/model/axes.py::LEGAL_LIFECYCLE`, `src/custom_memory_engine/schema/006_lifecycle_guard.sql::enforce_lifecycle_transition`, `src/custom_memory_engine/schema/006_lifecycle_guard.sql::enforce_provenance` | `tests/test_model_axes.py::test_transition_table_is_closed_and_has_no_promotion_from_inferred`, `tests/test_model_axes.py::test_reachable_lifecycles_per_kind_are_exactly_the_legal_ones`, `tests/test_model_axes.py::test_an_inference_cannot_become_a_fact_through_adjudication_for_an_abstraction`, `tests/integration/test_lifecycle_transitions.py::test_every_kind_and_every_lifecycle_pair_matches_the_model`, `tests/integration/test_lifecycle_transitions.py::test_raw_sql_under_a_non_superuser_application_role_cannot_promote_an_inference`, `tests/integration/test_lifecycle_transitions.py::test_an_inferred_row_without_a_derivation_fails_at_commit` | The closed, kind-conditioned transition table is now enforced by the database (migration 6, `E-016`): `inferred -> asserted` and any exit from `retired` raise for every writer, and an active `inferred` row needs a derivation row at commit. Still open: the **method** half of `inferred => derivation + method` and the adjudication, verification and reason-code records need `E-014`'s tables; channel assembly (`E-017`, `E-035`) is separate. | `E-014`, `E-017`, `E-035` |
| I2 | `PARTIAL` | `src/custom_memory_engine/schema/006_lifecycle_guard.sql::enforce_provenance`, `src/custom_memory_engine/schema/007_source.sql::sync_memory_evidence_source` | `tests/integration/test_migration_m4.py::test_invariant_1_holds_over_generalised_sources`, `tests/integration/test_migration_m4.py::test_backfill_is_byte_identical_and_hashes_match_the_python_canonicalisation` | Every `memory_version` must have an evidence row (a turn or an `external` source, `E-013`) or a derivation row at commit. Not yet covered: provenance of the other artifacts (`ASSOCIATION`, `ENTITY`, ...), which `E-014` creates. | `E-014` |
| I3 | `PLANNED` | | | The immutable method registry does not exist; its only writer is specified (`register_method`, admin-only, append-only, `V2-D46`). | `E-014` |
| I4 | `PLANNED` | | | The three-clock model needs `observed_at` and the `event_range` rename. | `E-012` |
| I5 | `PLANNED` | | | Scope-level exclusivity depends on the scope hierarchy; v1 `Invariant 4` covers the per-user case. | `E-011`, `E-012` |
| I6 | `PLANNED` | | | Retention orthogonality needs the retention axis and its reachability property test. | `E-010`, `E-018`, `E-020` |
| I7 | `PARTIAL` | `src/custom_memory_engine/scope/paths.py::subtree_predicate`, `src/custom_memory_engine/scope/access.py::read_predicate`, `src/custom_memory_engine/scope/access.py::rls_read_using`, `src/custom_memory_engine/scope/access.py::authorize_write` | `tests/integration/test_scope_matching_db.py::test_starts_with_predicate_is_exact_for_wildcard_identifiers`, `tests/integration/test_scope_access_db.py::test_sql_predicate_agrees_with_reference_semantics_on_every_row`, `tests/integration/test_scope_isolation.py::test_each_sql_matcher_fails_exactly_its_reported_cases`, `tests/integration/test_scope_isolation.py::test_rls_alone_returns_exactly_the_acl_for_every_caller`, `tests/integration/test_scope_isolation.py::test_rls_alone_honours_grants_never_private_and_revocation`, `tests/test_scope_isolation.py::test_every_application_read_set_is_contained_in_the_acl` | The scope primitives and both enforcement layers (`V2-D35`, `V2-D36`, `V2-D45`) exist and pass the `MA-011` isolation suite on scratch tables; no engine table carries a scope path yet, so production isolation is still per-user RLS (v1 `Invariant 7`). | `E-011` |
| I8 | `PLANNED` | | | Unbounded closure over the provenance graph; v1 bounds the walk at depth 3. | `E-037` |
| I9 | `PLANNED` | | | The W1 key (owner namespace, `V2-D51`) and the global lock order exist as a pure primitive (`scope.locks`, `tests/test_scope_locks.py`); no scoped table or advisory lock uses them yet. | `E-011`, `E-037` |
| I10 | `PLANNED` | | | Generalisation to `retired` and abstraction coverage. | `E-016`, `E-037` |
| I11 | `PLANNED` | | | Verification records and the disjointness check do not exist. | `C-010`, `C-021` |
| I12 | `PARTIAL` | `tests/test_boundaries.py::FORBIDDEN_MODULES` | `tests/test_boundaries.py::test_no_product_or_baseline_imports` | The boundary test forbids application, agent-framework and baseline modules but not model-provider SDKs. | `E-025` |
| I13 | `PLANNED` | | | Needs deletion ordering and closure-hash staleness. | `E-037`, `E-038` |
| I14 | `PLANNED` | | | Needs channel assembly and the fuzz suite. | `E-017`, `E-035` |
| I15 | `PLANNED` | | | Needs the opportunity ledger on the abstraction artifact. | `C-010`, `C-021` |
| I16 | `PLANNED` | | | Needs `NEGATIVE_CONSTRAINT` and the sticky-dissent path; the user's operation is specified (`dissent`, `V2-D46`). | `E-014`, `UM-012` |
| I17 | `PARTIAL` | `src/custom_memory_engine/scope/provenance.py::render_bundle` | `tests/test_scope_provenance.py::test_unreadable_source_renders_without_scope_id_or_type`, `tests/test_scope_provenance.py::test_handle_is_stable_per_caller_and_uncorrelatable_across_callers` | Per-caller opaque provenance handles exist (`V2-D37`); retrieval output does not use them yet, and entity opacity needs `ENTITY`. | `E-011`, `E-014`, `E-017` |
