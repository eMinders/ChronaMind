# Pre-W1 Refactoring and Cleanup Report (r10)

Everything changed in r10 other than the security/correctness fixes themselves (which are in
`SECURITY_AND_CORRECTNESS_REVIEW.md`). Each item states why it was changed and how it was checked.

## 1. Refactoring

| Change | Why | Verification |
|---|---|---|
| **One schema source.** `migrate.migration_scripts()` is the only way to obtain ordered migration SQL; `tests/integration/schema.py::apply_migrations` uses it for every integration suite, and the Suite D harness uses it too | Nine suites and the H4 harness read `001_full_schema.sql` themselves (two also `002`), so they ran on a schema production never runs, and each W1 migration would have been untested by them | Full suite on the migrated schema; Suite D dev reports byte-identical |
| Dead constants removed from 13 suites (`SCHEMA_SQL`, `SCHEMA_DIR`, `MIGRATION_FILES`) | Replaced by the helper | ruff, pyright |
| **Least-privilege matrix as data.** `provisioning.TABLE_GRANTS` replaces the uniform `USER_TABLE_GRANTS`/`CATALOG_GRANTS` | Grants now differ per table; one matrix mirrored by `rls.sql`, compared exactly by a test | `test_rls_sql_grants_equal_the_least_privilege_matrix_exactly` |
| Migration runner logs the applied version | Logged a module constant | `test_migrations.py` |
| Upgrade test rewritten to install only migration 1 and upgrade | It faked the old state by dropping objects, which cannot express migration 4 | passes; data retained |
| **Deletion scanner composition** (`_supplemented`) | A caller's channel-1 probe replaced the storage scan | regression test |
| `engine/gate.py`: dead `classify_assertion` parameters removed | Abandoned API, never read | `test_gate.py` |
| `security/audit_guard.py` (new) | One allowlist instead of ad-hoc dicts and a three-key blacklist | `test_audit_guard.py` |

## 2. Deleted

| File | Why deleted | References checked |
|---|---|---|
| `docs/Graduation_Project_Proposal.md` | Pre-V1 draft (779 lines) that **differs** from the frozen, hash-protected proposal in `custom-memory-engine/` (858 lines): a competing source of truth | none |
| `docs/ChronaMind_Master_Integration_Blueprint.md` | Plans ChronaMind inside the product app; contradicts `INDEPENDENCE_BOUNDARY.md` and the V2 architecture | none |
| `docs/atlas-cloud-logo.png`, `docs/atlas-cloud-logo-dark.png` | Third-party advertisement assets | only the advertisement block, removed |
| `custom-memory-engine/PLAN/GITHUB_SETUP.md` | Unreferenced v1 label how-to naming a directory (`research-lab/`) that no longer exists | none |
| `custom-memory-engine/PLAN/templates/*.md` (4) | Never wired into GitHub; the PR template prescribed `Closes #<issue>`, the cause of r8's accidental issue close | `CONTRIBUTING.md` and `research/benchmarks/README.md` repointed |

Git history retains every deleted file.

## 3. Rewritten

- Root `README.md`: says what the repository is (engine + product application, independent) instead of the
  upstream template's title and an advertisement with model-catalogue tables.
- `PLAN/CONTRIBUTING.md`: `Refs #N` and manual closure after verifying acceptance criteria, instead of closing
  keywords.
- `schema/README.md`, `security/README.md`: migrations 1-4, twelve tables, exact grant matrix, new evidence.

## 4. CI/CD

| Change | Reason |
|---|---|
| `ci.yaml` job `workflows`: actionlint pinned by image digest | workflow files were never validated in CI |
| `tests/test_ci_workflow.py` | pins every gate, pinned images/actions and the read-only token; removing a gate fails a test before it can reach `main` |
| `.github/workflows/README.md` | lists the new job and the full static-gate list |
| Caching | verified working from run logs (uv cache hits); unchanged |

## 5. Plans and records

| Document | Change |
|---|---|
| `DECISIONS_V2.md` | §7: `V2-D54` (tenant integrity), `V2-D55` (v1 gaps closed and defects fixed), `V2-D56` (W1 requirements; status claims) |
| `THREAT_MODEL.md` | T-24-T-30; accepted-risk row for v1 evidence-turn retention |
| `MIGRATION_RISK.md` | D14 (W1 numbering, composite keys, pinned functions) |
| `ROADMAP_V2.md` | r10 requirements binding every W1 task; `E-037` obligations |
| `INVARIANT_LEDGER.md` | every v1 row `ENFORCED`; new `Invariant 7 / database integrity` row |
| `TASK_GRAPH.md`, `status_evidence.json`, `STATUS_AUDIT.md` (generated) | `RL-005`, `RL-071`, `RL-072`, `RL-090`, `RL-091`, `RL-092` reconciled; 0 `claimed-unverified`, no discrepancies |
| `TRACEABILITY_MATRIX.md` | Invariants 8-10 rows updated (the frozen record files are untouched; `document_hashes.txt` 5/5) |
| `audits/` | six r10 reports; earlier audits kept as cited evidence and marked historical in the index |
