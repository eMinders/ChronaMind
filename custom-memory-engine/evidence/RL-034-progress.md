# RL-034 software implementation status — 27 October 2026

**In progress.** Numerical primitives and H1/H2/H4 decisions are implemented.
The user is asking the leader to clarify H3's undefined crossing-zero ASR cases;
the final H3 verdict evaluator is deliberately pending. This is neither a
STAT-UNIT completion claim nor a pilot/confirmatory research result.

## Hard-review correction

The subsequent hard review reproduced two false-positive paths in the earlier
source commit: rounding paired scores before averaging could manufacture a
positive contrast from identical run/item score multisets; a NumPy reduction
could also move a constant `−0.1` bootstrap lower bound above the exact `−0.1`
NI margin. New failing regressions established both defects before correction.
Aggregation now preserves exact binary-input sums through each independent
cluster, and bootstrap resamples divide exact integer sums once. Fraction-based
oracles cover cancellation, subnormals, run/item permutations and constant
bootstrap distributions. H1 also rejects a question appearing in both exclusive
strata. Latest focused non-CP tests:182 passed; the submitted revision's complete
CI must be inspected separately from the earlier commit counts below.
An additional exact-threshold check found a one-bit difference between default
CP APIs caused by floating subtraction of the confidence level. Both frozen
default paths now use the identical literal 0.025 tail; a regression checks H4
at and immediately below that bound for every N20–100. Earlier verification
counts below remain dated evidence for their named commits.

## Requirement coverage

| Requirement | Implementation / portable evidence |
| --- | --- |
| Paired whole-cluster percentile bootstrap, B≥10,000, explicit seed | `evaluation/stats/bootstrap.py`; unequal cluster sizes, complete identity join, run averaging, independent seeded-index oracle and row-permutation tests in `test_bootstrap.py` |
| Correct independent units | Scenarios for T/G; question=cluster for LongMemEval_S; conversations for LoCoMo. `models.py` rejects duplicate/missing pairings and incomplete run grids. LoCoMo has no permutation test or confirmatory verdict. |
| Two-sided paired sign flips, exact/Monte Carlo | `permutation.py`; exhaustive rational/itertools oracles, six/seven-cluster feasibility arithmetic, zero/tiny/tied inputs, integer-lattice tail comparisons, deterministic PCG64 and plus-one randomized p-values in `test_permutation.py` |
| Global Holm family of three | `multiplicity.py` combines H2 by max-p before global Holm; `test_multiplicity.py` checks an independent closed-Bonferroni-intersection oracle; `family.py` refuses a missing superiority input. NI endpoints add no extra test. |
| Exact CP and frozen output | `clopper_pearson.py`; all 4,941 (N,k) combinations for N20–100 checked against the existing elementary binomial-sum/bisection oracle, entire formatted `validation/cp_bounds.output.txt` reproduced, high-precision Decimal anchors, independent root-solver/CDF inversion, coverage/symmetry/tiny-tail/invalid-input checks |
| H1/H2/H4 mechanical decisions | Strict p/CI/NI boundaries, feasibility/precision guards, shared source identities, H4 scenario counts and positive control conditions tested. H2 reconciles the full Proposal row: an interval containing zero is inconclusive; both conservative intervals must be above zero for support. |
| H3 final decision | **Pending leader clarification.** `h3_conditions` exposes the specified ASR and LUAR components without inventing a final outcome. |
| Persisted evidence integrity | Input/effect hashes, sample/summary counts, predeclared CI levels, NI consistency, role/freeze/p-value/precision and H4 count/CP/verdict contradictions are validated; regressions in `test_evidence_integrity.py`, `test_family.py`, `test_decisions.py`. |

## Verification recorded so far

- CP module: 231 tests passed, including exhaustive N20–100 arithmetic and nominal
  coverage checks; scoped Ruff and Pyright passed.
- Latest combined non-CP statistics tests: 152 passed. Total statistics collection:
  383 tests. Two independent review passes reproduced and corrected source-mix,
  descriptive-only reporting and persisted-summary defects; method probes and
  strict H1/H2/H4 boundary checks passed after correction.
- Submitted source commit `cad63350c1dfe5b30b44be93bdf50d8552947f19` passed
  all six checks: both Linux full-service jobs **1,060 passed**, zero failures or
  skips; both default-install jobs **988 passed, 56 expected skips**. Engine
  runs [40429405393](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40429405393)
  (PR) and [40429332734](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40429332734)
  (push) also passed reduced SQL and reference checks. The root checks and review
  workflow succeeded; no formal/inline review or review comment was present at
  inspection, so workflow success is not described as leader approval.
- Local full-service run: **1,047 passed**, 42 marked integration tests passed,
  zero failures/skips/errors. It collected before the last 13 persisted-summary
  regression additions; latest focused tests and the submitted-head Linux runs
  include them. Owned PostgreSQL containers/networks/volumes remaining: **0/0/0**;
  owned Falkor graph list empty and container removed. Local default:975 passed,
  56 skips; all six optional model/vendor packages confirmed absent. Latest
  non-CP383-minus231=152 statistics checks also pass in that default environment.
- Engine Ruff lint/format and Pyright passed; root Ruff compatibility was checked
  with its older pinned tool. Mechanical/reference CP checks passed. Only the
  changed changelog's canonical LF document checksum was updated.

Environment: isolated engine CPython3.12.13, NumPy2.4.2, SciPy1.18.1,
Pydantic2.12.5, Ruff0.15.6, Pyright1.1.408; the separate default install uses
CPython3.12.12. SciPy was added from the official project/PyPI release, locked
under the engine manifest only. No product files/environment, canonical DDL,
frozen split assignments, proposal or checklist were changed.

## Remaining work

Resolve the H3 protocol clarification, implement and test its final three-way
decision, reconcile resulting documentation, verify the final submitted head
and complete independent final review. The counts above describe the named
source commit; later documentation-only heads still receive CI checks. Leader
review/merge and the project's research approval/freeze gates remain separate.
