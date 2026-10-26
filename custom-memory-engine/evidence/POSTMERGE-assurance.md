# Post-merge assurance for RL-031 and RL-041

Reviewed on 27 October 2026 against merged main `c18b9fd`, preserving the team's
PR #36 and PR #37 changes. This follow-up addresses concrete findings in existing
contributions; it does not claim another team's issue.

| Finding or invariant | Direct verification |
|---|---|
| Replacement adapter cleanup | Two regressions fail against merged main: close counts are `[2,0]` for two instances on success and second-case failure. The bounded child resource stack gives `[1,1]`; failed runs cannot publish a success manifest. |
| Replay retention and accounting | Reset/release remain local. Tests repeat same-user and distinct-case replay, retain another active tenant, reject failed retirement before new model work, and preserve retryable owned scopes. Completed question diagnostics fold into counters. |
| Native backend retirement | Actual Mem0/Qdrant/SQLite instances close per generation; actual PostgreSQL deletes check ownership plus generation/user; actual Graphiti retirement releases only owned graphs/clients and parent scope tracking waits for an exact acknowledgement. |
| Exact long-history context | Oldest whole-turn selection matches an exhaustive oracle. The frozen codec certifies regex boundaries and counts segments once; complete original/selected requests are checked. Unknown or uncertified codecs retain the exact scan, including nonmonotone and empty-encoding cases. |
| Work growth | At 500/1,000/2,000 fixture turns, tokenizer work falls from 28.2M/112.8M/452.3M characters to 0.24M/0.47M/0.92M with identical selected turns and diagnostics. Independent frozen-codec probes compare 1,287 Unicode/window-boundary selections without mismatches. These are work counters, not latency benchmarks. |
| Migration drift detection | Real PostgreSQL probes reproduce false healthy states after moving an FK to another relation or disabling internal enforcement triggers. Both new regressions fail against the original catalog queries and pass after recording stable relation/trigger semantics. Reset restores missing-parent rejection. |
| Migration failure handling | Advisory-lock timeout SQLSTATE 55P03 and cancelled install leave no partial schema and permit retry. Existing apply/reset rollback, concurrency and full-table 43-assertion checks remain required. |

The standalone corrected migration suite passed 430 tests with no skips/failures,
including 39 cases marked integration (21 new migration database cases); the full
post-reset and separate reduced SQL checks each passed 43 assertions. The two
catalog regressions fail against the original fingerprint queries (41 pass / 2
fail).

The combined main-based follow-up passed **677/677 tests**, including **42/42
cases marked integration**, with zero failures, skips or collection errors on
locked CPython 3.12.12. This run included all native backends and existing frozen
model/tokenizer assets; Python source hashes were unchanged across verification.
The post-reset full-schema test and separate reduced SQL check each passed all
**43 assertions**. PostgreSQL cleanup verified zero owned containers, networks or
volumes. The owned Falkor graph list was empty before removing its container.

A separate frozen default environment verified that all six optional SDK packages
were absent and passed **605 tests with 56 explicit skips**, zero failures/errors.
Those skips cover absent PostgreSQL/Falkor services, assets and optional SDKs;
they are not counted as native integration coverage. Both environments preserved
root and engine manifests/locks. Engine/root lint, formatting and types, all five
canonical reference hashes, mechanical checks and exact Clopper–Pearson output
passed. Root types used the existing root interpreter explicitly because this
isolated checkout has no root product environment.

Earlier attempts were not treated as passes. An external progress logger collided
with a test's intentional file-access block and was removed. Missing/noncanonical
ignored corpus caches were replaced with byte-verified copies of existing local
assets, and one new test's Redis bytes/text comparison was corrected. The final
677-test run began with stable verified caches; no corpus content was regenerated
or downloaded. These recoveries did not alter product behavior or waive tests.

Verification uses the existing locked CPython 3.12.12 environment with baseline
extras, pinned PostgreSQL 16.15 / vector 0.8.7 / btree_gist 1.7, FalkorDB 6.0.3 and
immutable local model/tokenizer assets. All provider boundaries in these tests
are scripted; real SDK/storage/model implementations execute locally. No paid
provider, pilot or confirmatory research run occurred. D-21 and runtime/RLS,
deletion and load gates remain separate. Root product configuration and dependency
locks are unchanged.

The earlier pre-merge catalog fingerprint reports drift under the revised runner.
Apply does not silently replace history. Confirmed reset is for disposable data;
retaining existing rows requires an operator-reviewed migration.
