# P0, P1, and Wave 2 (`#14`–`#19`) verification report — 2026-10-25

## Scope and reproducibility

Reviewed against repository base commit `cdabcf3`; this report is included in the verified implementation commit. Commands are in the [README](../README.md). Dependency graph: `../uv.lock`; CPython 3.12.12, Linux x86_64. A **fresh isolated Docker Compose container** was started from:

`pgvector/pgvector:0.8.7-pg16-bookworm@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a`

Docker image ID matched the selected digest on the checked platform. SQL reported PostgreSQL `16.15 (Debian 16.15-1.pgdg12+2)`, vector `0.8.7`, btree_gist `1.7`. A pre-existing database was inspected during reconnaissance but **not used for the final verification run**. No product database was modified. Fixture schemas and the dedicated verification container were removed after testing.

## Executed results

| Check | Result | Scope |
|---|---|---|
| Full pytest suite | **67 passed**, no skips or warnings | Protocol, pilot rules (`RL-011`), public benchmarks (`RL-020`, `RL-021`), own suites `G/I/D/T` (`RL-022`–`RL-024`), config, AST boundary/link checks (`RL-006`), and 5 real-DB async compatibility tests |
| Ruff check / format check | Passed | Unified `custom-memory-engine` package, benchmark, and test modules |
| Pyright standard mode | **0 errors, 0 warnings** | Unified `custom-memory-engine` package, benchmark, and test modules, Python 3.12 |
| Lab mechanical consistency script | All hard checks passed | Proposal/checklist/changelog; frozen reference hash unchanged |
| Lab document MD5 integrity | All 5 files `OK` | `md5sum -c validation/document_hashes.txt` |
| Lab CP output replay | Byte-identical diff | Existing scenario arithmetic |
| Lab reduced bitemporal SQL | **43 PASS assertions**, final ALL ASSERTIONS PASSED | Historical reduced schema only, re-executed on fresh selected image |
| Public benchmark checksums & counts | Verified | `LongMemEval` (`oracle` & `S`, `500 / 30 / 56`) and `LoCoMo` (`10 / 1,986 / 4`) |
| Own Suites `G, I, D, T` determinism | Verified | Byte-identical seeded generation (`20` dev / `50` test scenarios) and `MANIFEST.txt` SHA-256 match |

Captured outputs (trailing console padding stripped, substantive output unchanged): [pytest](pytest.txt), [quality](quality-checks.txt), [mechanical checks](mechanical-checks.txt), [reduced SQL](bitemporal-baseline.txt). Timings in pytest output are test execution timings, **not engine latency benchmarks**. GitHub Actions passed on commit `577196c` ([run 40115984039](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40115984039)). The remote run exposed Node 20 deprecation/ubuntu-latest migration notices; the follow-up pins Node 24 actions and Ubuntu 24.04 to avoid those known lifecycle warnings.

The first integration run exposed a **test assertion defect**: PostgreSQL's human-readable server_version includes the Debian package suffix. The probe now asserts numeric server_version_num `160015`, plus exact extension versions. All tests were rerun after the correction; no failed result is presented as success.

## What these checks establish

- Psycopg async native `tstzrange` and pgvector 384-dimensional vector adaptation work with the selected stack.
- HNSW index creation and iterative-scan configuration are supported; the small probe does not prove filtered approximate recall.
- Transaction-local clock GUCs clear after commit **and rollback** on pooled reuse.
- Exclusion prevents overlapping believed single-value rows; close-then-insert succeeds; multi-value rows remain exempt as specified.
- Two independent connections demonstrate advisory lock exclusion/release and SKIP LOCKED job selection. A conflicting writer race permits only one committed overlapping row.
- Stable session ordering, preserved source timestamps, strictly increasing recorded clocks, reference clamping, input guards, CP arithmetic and fail-closed binary judge gates behave as unit-tested.
- No product/baseline imports are introduced in the new implementation package; six canonical memos and decision links exist.

## What remains unverified or pending

1. Human owner/peer/supervisor approvals for downstream research execution, funding/license authority and any institutional ethics determination remain pending. #1–#5 satisfy their preparation/verification acceptance criteria (they require recording sign-off status, not fabricating approval); #6 still requires actual Member 3/4 co-signatures and cannot be closed.
2. Model API availability/access, extraction schema compliance, embedding inference/hardware throughput and actual model/provider costs. Sources confirm published metadata only; no paid inference occurred.
3. Exact S/M benchmark anomaly counts, dataset adapters/generators, test split freezes and human judge agreement. No benchmark datasets/test splits or participant data were accessed.
4. Full 12-table engine/migrations, runtime-role RLS, transactional archival T1–T5 and seven-channel deletion. The historical 43-assertion SQL deliberately allows an archival case invalid under V4; it is not full-engine acceptance.
5. Eight-worker load, retries under serialization/deadlock pressure, queue durability, production observability/redaction, fault recovery and measured capacity. There is **no production-readiness or high-performance claim** for an engine not yet implemented.
6. Higher-authority Proposal vs Technical Specification statistical discrepancies, documented in [SOURCES](SOURCES.md), must be resolved before RL-034.

All six uploaded tasks have technical decision deliverables in the user-requested root location. #1–#5 are eligible for preparation-task closure after push; #6 remains blocked on its explicit co-signature checkbox. User authorization permits direct-to-main delivery rather than a PR and supersedes old Lab-local memo paths. This report is evidence for P1 compatibility, not a substitute for future production acceptance.

## Review fixes and additional verification

- Validated Agreement counts, sample size and derived metrics; reject non-finite, inconsistent, negative or extra fields. Kappa calculation uses integer arithmetic before final division; label validation avoids allocating a combined copy of input sequences.
- Replay rejects duplicate session IDs and normalizes sort keys to UTC; mixed-offset equivalent instants retain source order.
- Configuration holds DSN in SecretStr, suppresses input values in validation error text, and restricts probes to the explicitly named local disposable database. A private `.env` was created with a generated 256-bit test password, mode 0600, and verified Git ignore rules. No provider secrets are required.
- Connections close on setup failures and use a connection timeout; pool rollback verification now uses autocommit connections so the test exercises full transaction rollback rather than a nested savepoint.
- Conflicting writer test waits for server-side Lock contention before releasing the first transaction; pending task cleanup is bounded. Warning-free tests are enforced in pytest configuration.
- Built wheel and source distribution successfully into a temporary directory. Reviewed package contents, environment exclusions, Git staging and issue acceptance scope. Local test database is removed after verification.
