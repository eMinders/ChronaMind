# r7 — Blocker Resolution and V1 Completion Report

**Date:** 2026-10-31. **Authority:** explicit Project Owner authorization to resolve
B-01–B-09, complete #46/#47, and deliver directly to `main`.
**Baseline:** `main` @ `490904d`. **Decisions:** `DECISIONS_V2.md` §5.

This report keeps four states distinct:

- **Verified** — implemented on `main`, tested, CI green.
- **W1 integration** — the primitive is verified; a named W1 task wires it into tables that `M1`–`M9` have not created yet.
- **Deferred** — explicitly postponed, with a decision.
- **External** — needs a human or a resource the engineer does not have.

---

## 1. Blockers

| ID | Decision | State | Primitive (code) | Verification | Remaining (owner) |
|---|---|---|---|---|---|
| B-01 | `V2-D35` literal prefix, never `LIKE` | **Verified** | `scope/paths.py` | `test_scope_paths.py` (32), `integration/test_scope_matching_db.py` (11; reproduces the PG16 leak) | RLS predicate on scope columns: W1 integration (`E-011`) |
| B-02 | `V2-D36` minimal ACL | **Verified** (storage + authorization) | `002_scope_grants.sql`, `scope/access.py`, multi-version `migrate.py` | `test_scope_access.py`, `integration/test_scope_access_db.py` (restricted role; SQL = reference on every row), `integration/test_migrations.py` | Apply `read_predicate` to rows once `M2` exists: W1 integration (`E-011`) |
| B-03 | `V2-D37` opaque HMAC handle | **Verified** | `scope/provenance.py` | `test_scope_provenance.py` (adversarial, 3 tenants) | Bundle rendering: W1 integration (`E-017`) |
| B-04 | `V2-D39` §8.1 canonical; no `retract` | **Verified** | `api/contract.py`, `API_V2.md` (from PR #92) | `test_api_contract.py` (parses both documents; mutation fails 5 tests) | Runtime API layer: future waves |
| B-05 | `V2-D38` `archived` → `retired`/`durable` | **Verified** | `model/axes.py` | `test_model_axes.py`, `integration/test_model_backfill_db.py` (40/40) | Add axis columns and run the backfill: W1 integration (`E-010`) |
| B-06 | `V2-D40` bound gates verification | **Verified** | `soundness/admission.py` | `test_soundness_admission.py` (146; equal to both references) | `infer`/`verify` operations: W1 integration (`E-015`) |
| B-07 | `V2-D41` per-type grounding | **Verified** | `grounding/sources.py` | `test_grounding_sources.py` (turn rule identical to the v1 gate) | `SOURCE` table: W1 integration (`E-013`, `E-015`) |
| B-08 | `V2-D13` cap 32 (DB) + `V2-D43` | **Verified**; `M5` **Deferred** | `003_association_cap.sql`, `model/association.py` | `integration/test_association_cap_db.py` (concurrency) | Attach to `ASSOCIATION`: W1 integration (`E-014`) |
| B-09 | `V2-D42` gate G-TS | **Verified** (J5 + coverage in CI) | `evaluation/harness/split_gate.py`, `scripts/check_test_split_gate.py` | `test_split_gate.py` | Signed `PilotFreeze` (three human sign-offs): **External** — only needed before the first confirmatory run (G5) |

**Why W1 integration is not done here.** Wiring into `scope_path`/`visibility`, `SOURCE`,
`ASSOCIATION` and the lifecycle columns needs those structures, which are created by
`M1`–`M9` in `E-010`…`E-014`. Each of those is a large W1 task with its own migration-risk
review (PR #89, `MA-016`). Implementing W1 wholesale exceeds "prerequisite work for these
corrections". Executing it hastily would also risk the isolation guarantee, since RLS rewrites
across 12 tables are exactly where cross-tenant bugs live. The primitives remove every
*design* blocker. W1 now has tested, exact functions to call.

---

## 2. Retained V1 requirements

### #46 — deletion completeness (`RL-070`): **all acceptance criteria met**

| Acceptance criterion | Evidence |
|---|---|
| Under `P`, suite `D` **dev**: all seven channels measured and complete, zero residue | `reports/deletion/suite_d_dev_P.json`: 0/20 scenarios leak, 0 incomplete measurements, 0 never-stated false positives |
| Under `A8`: residual leakage detected (positive control) | `suite_d_dev_A8.json`: 20/20 scenarios leak. Every per-event leak set equals the generator's `expected_a8_leak_channels` (0 mismatches); channel 4 is detected in 40/40 lineage events |
| Per-channel counts and the scenario-level CP bound, machine-readable | Both reports; `P` bound = 0.168433 = the dataset's declared `zero_leak_cp_975_upper_bound` |
| Test splits not run | `deletion_suite.load_split("test")` raises; split regenerated and checked against `MANIFEST.txt` |
| Suite green; deletion/scanner tests changed only by addition | No existing deletion test was edited |

The runner found a **real v1 defect**. Deleting any belief that had been updated through the
gate failed with a foreign-key violation from `memory_conflict.existing_version_id`, so R6
deletion of superseded beliefs did not work through the normal write path. It is fixed in
`engine/deletion.py`, and the regression test is `integration/test_deletion_conflict_db.py`.

**Measurement substitutes (stated, not hidden):**
- Channel 6 uses an extractive answerer, which returns the assembled context. This is a conservative upper bound, because the canaries are high-entropy fictional strings.
- Summary re-derivation is extractive over the surviving sources.
- The frozen `BAAI/bge-small-en-v1.5` model (hash-verified assets) drove channel 3 and retrieval in the recorded run. CI uses a deterministic bag-of-words embedder.

### #47 — RLS and the restricted role (`RL-071`): **all acceptance criteria met**

The work builds on PR #87's commit, cherry-picked with authorship preserved, plus the gap fixes:

| Acceptance criterion | Evidence |
|---|---|
| Role without `BYPASSRLS`; `FORCE` verified on every user table | `provision_runtime_role` → `verify_forced_rls` + `verify_runtime_role` (now 12 tables, including `scope_grant`) |
| Guard rejects invalid contexts before any query; transaction-locality | `tests/test_security_scoping.py`, `integration/test_rls_isolation.py` |
| Isolation under the shipped role: direct queries, hybrid retrieval, suite `I` | `integration/test_rls_isolation.py` |
| Suite `I` exposure = 0 with its exact CP bound | 0/20 exposed scenarios, one-sided 97.5% bound 0.168433 |
| Privilege catalog assertion, no extra grants | `privilege_violations` / `verify_privileges` (new); detects extra `TRUNCATE`, a missing `DELETE`, and schema `CREATE` |
| Write and delete paths work under the role | Gate supersession and deletion as the role; cross-tenant deletion → `InsufficientPrivilege` |
| Superuser suite unchanged | Full suite green |

---

## 3. Verification (fresh clone of the committed HEAD, CI-equivalent)

| Check | Result |
|---|---|
| `verify` job (extras, PostgreSQL 16, FalkorDB, frozen assets) | **1792 passed, 0 skipped** (baseline at `490904d`: 1402 passed, 21 skipped) |
| `default-install` job (no extras, no services) | **1558 passed, 218 skipped**: integration tests skip without a database, as in CI |
| `ruff check` / `ruff format --check` | clean |
| `pyright` (project config) | 0 errors |
| `validation/bitemporal_checks.sql` | ALL ASSERTIONS PASSED |
| `md5sum -c validation/document_hashes.txt` | 5/5 OK (frozen research record untouched) |
| `validation/mechanical_checks.py` | ALL HARD CHECKS PASSED |
| `cp_bounds` diff | identical |
| `scripts/check_statistics.py` | ALL STATISTICS ASSERTIONS PASSED (now a CI step) |
| `scripts/check_test_split_gate.py` | G-TS integrity PASSED (now a CI step) |
| Pre-existing tests modified | only `tests/integration/test_migrations.py` (adapted to a three-version history with the same intent, plus gap, rename, per-file drift and upgrade cases); every other touched test file is new |
| Installed wheel | migrations 1–3 ship and apply from site-packages |
| GitHub Actions | green on every pushed head except `85b2c06`. That run failed because the Suite D runner read a gitignored local file; it was fixed in `0846330`, and verification now runs from a fresh clone |

---

## 4. Not done, and why

- **W1 integration** of the eight primitives (table above): the next W1 tasks own it.
- **`G0b` is not declared.** Its decision clause is now satisfiable, but it also requires
  `MA-013` frozen (PR #91 open) and `MA-017` green (not implemented).
- **External:** the signed `PilotFreeze` (J7/J8) needs three distinct human sign-offs.
  No confirmatory test split has been or can be read.
- **Open decisions not taken:** `V2-D14`, `D16`, `D17`, `D30`, `D31`, `D32`, `D34`. They
  were outside B-01–B-09 and are not needed before W1.
- **Open PRs:**
  - Not merged: #82–#86, #88–#91. #87 and #92 had their commits cherry-picked; the PRs themselves were not merged or closed.
  - A comment on each of #87 and #92 records where their work landed.
