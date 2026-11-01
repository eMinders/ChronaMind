# Final Pre-W1 Verification (r10)

Fresh-clone verification of `main` after every r10 change. Run on a clean clone from `origin`, with a **new**
disposable PostgreSQL 16 (pgvector 0.8.7, `tests/init_extensions.sql`) and FalkorDB 6.0.3, and freshly prepared
model assets: nothing reused from the development checkout.

## 1. Procedure

```bash
git clone https://github.com/Abdulrahman-Ayman-Farid/ChronaMind.git && cd ChronaMind/custom-memory-engine
uv sync --frozen --extra baselines --python 3.12.12
uv run --no-sync python -m evaluation.baselines.assets prepare --directory "$CME_BASELINE_TEST_ASSETS"
uv run --no-sync pytest -q                                   # full CI scope (DB + FalkorDB + assets)
uv run --no-sync ruff check src tests research/benchmarks evaluation scripts
uv run --no-sync ruff format --check src tests research/benchmarks evaluation scripts
uv run --no-sync pyright
psql ... -f validation/bitemporal_checks.sql                # ALL ASSERTIONS PASSED
md5sum -c validation/document_hashes.txt                     # frozen research records
python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md
diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt
uv run --no-sync python scripts/check_statistics.py
uv run --no-sync python scripts/check_test_split_gate.py
python3 scripts/check_plan_refs.py && python3 scripts/audit_status.py --check
uv run --no-sync python scripts/plan_coverage.py --check
docker run --rm -v "$PWD/..:/repo" -w /repo rhysd/actionlint:1.7.7   # workflow validation
# plain install, no database or extras:
uv sync --frozen --python 3.12.12 && uv run --no-sync pytest -q
```

## 2. What each check covers

| Requirement | Check |
|---|---|
| Security and authorization | `test_tier_permissions.py`, `test_api_contract.py`, `test_security_scoping.py`, `integration/test_rls_isolation.py`, `integration/test_tenant_integrity_db.py` |
| Tenant isolation | `test_scope_isolation.py`, `integration/test_scope_isolation.py`, `integration/test_scope_access_db.py`, Suite I probes |
| Concurrency and locking | `test_scope_locks.py`, `integration/test_clock_queue_db.py`, `integration/test_queue_regressions_db.py`, `integration/test_association_cap_db.py` |
| Migration validation | `integration/test_migrations.py` (install, upgrade from migration 1, drift, reset), catalog rules |
| Invariants 8-10 | `test_audit_guard.py`, `integration/test_audit_content_db.py`, `integration/archival/test_t1_to_t5.py`, `integration/test_replay_gate_db.py`, Suite D |
| Bitemporal assertions | `validation/bitemporal_checks.sql` and `integration/test_full_schema.py` (43 on the full schema) |
| Frozen research records, statistics, G-TS | `document_hashes.txt`, mechanical checks, CP bounds, `check_statistics.py`, `check_test_split_gate.py` |
| Plans and ownership | `check_plan_refs.py`, `audit_status.py --check`, `plan_coverage.py --check` |
| CI gates themselves | `test_ci_workflow.py`, actionlint |

## 3. Results

Recorded below after the last code change merged, against the exact commit verified.
