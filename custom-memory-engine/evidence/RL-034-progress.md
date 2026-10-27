# RL-034 software implementation status — 27 October 2026

**Software implementation complete.** Numerical primitives and H1–H4 decisions
are implemented and mechanically tested. This is not a pilot/confirmatory
research result or research approval.

## H3 protocol completion

H3 now has a deterministic three-way decision for every valid input. Infeasible
analysis is `Inconclusive`; failure to establish ASR reduction is `Not supported`;
LUAR loss entirely beyond the frozen margin is `Not supported`; strict LUAR
non-inferiority with established ASR reduction is `Supported`; exact margin
contact and remaining LUAR uncertainty are `Inconclusive`. The ASR rule treats
an interval crossing or touching zero as failure to establish reduction. Tests
cover crossing-zero, positive/non-significant, p-value, precision, excessive
loss, strict support, strict straddle, and exact-margin boundaries.

Earlier verification records below remain historical evidence for their named
source commits; final-head checks are recorded after the implementation commit.

Source commit `2eba86af9750bf027ee54b4ee8d5d02cb8cf8034` adds the four
unambiguous H3 branches, preserving a pending result for undefined cases.
Current statistics collection:427 tests; non-CP196 passed. Independent review
passed22 focused tests,25 H3 cases (19 full-pipeline and six interval-boundary
fixtures), five family states and16
persisted-record rejection probes. It found no material defect in these branches.
Both full-service CI runs passed **1,104 tests**, with zero skips/failures;
both default installations passed **1,032 tests, 56 expected skips**. All six
checks passed, including SQL/reference checks, root checks and the automated
review workflow. See [PR engine run](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40443418187)
and [push engine run](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40443412306).
The following evidence-only update receives separate exact-head checks.

## Earlier hard-review correction

The subsequent hard review reproduced two false-positive paths in the earlier
source commit: rounding paired scores before averaging could manufacture a
positive contrast from identical run/item score multisets; a NumPy reduction
could also move a constant `−0.1` bootstrap lower bound above the exact `−0.1`
NI margin. New failing regressions established both defects before correction.
Aggregation now preserves exact binary-input sums through each independent
cluster, and bootstrap resamples divide exact integer sums once. Fraction-based
oracles cover cancellation, subnormals, run/item permutations and constant
bootstrap distributions. H1 also rejects a question appearing in both exclusive
strata. That revision's focused non-CP tests:183 passed; its collection contains
414 statistics tests. That corrected source commit's CI results are below.
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
| H3 final decision | **Complete.** `evaluate_h3` classifies every valid input using the frozen three-way protocol; assessment and family JSON validators bind each result to its conditions and freeze reference. |
| Persisted evidence integrity | Input/effect hashes, sample/summary counts, predeclared CI levels, NI consistency, role/freeze/p-value/precision and H4 count/CP/verdict contradictions are validated; regressions in `test_evidence_integrity.py`, `test_family.py`, `test_decisions.py`. |

## Verification recorded so far

- CP module: 231 tests passed, including exhaustive N20–100 arithmetic and nominal
  coverage checks; scoped Ruff and Pyright passed.
- Earlier combined non-CP statistics tests:183 passed; CP plus affected decision
  and rounding tests:281 passed. Independent Fraction checks covered 429 constant
  bootstrap cases, 660 random means and 96 nested weighting cases, mixed
  subnormal distributions and larger seeded/unbatched bootstrap oracles.
- Source commit `df074f7bdcc9e66c2e6132723a64a4c0e414d4d6` passed all six
  checks. Both Linux full-service jobs: **1,091 passed**, zero failures/skips.
  Both default-install jobs: **1,019 passed, 56 expected skips**. The
  [PR engine run](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40441046220)
  and [push engine run](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40441042005)
  passed SQL assertions and reference consistency; root and automated review
  workflows succeeded. No formal review approval was present at inspection.
- Earlier independent review passes reproduced and corrected source-mix,
  descriptive-only reporting and persisted-summary defects; method probes and
  strict H1/H2/H4 boundary checks passed after correction.
- Earlier source commit `cad63350c1dfe5b30b44be93bdf50d8552947f19` passed
  all six checks: both Linux full-service jobs **1,060 passed**, zero failures or
  skips; both default-install jobs **988 passed, 56 expected skips**. Engine
  runs [40429405393](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40429405393)
  (PR) and [40429332734](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/actions/runs/40429332734)
  (push) also passed reduced SQL and reference checks. The root checks and review
  workflow succeeded; no formal/inline review or review comment was present at
  inspection, so workflow success is not described as leader approval.
- Earlier local full-service run: **1,047 passed**, 42 marked integration tests passed,
  zero failures/skips/errors. It collected before the last 13 persisted-summary
  regression additions; latest focused tests and the submitted-head Linux runs
  include them. Owned PostgreSQL containers/networks/volumes remaining: **0/0/0**;
  owned Falkor graph list empty and container removed. Local default:975 passed,
  56 skips; all six optional model/vendor packages confirmed absent. The then
  152 non-CP statistics checks also passed in that default environment. The hard
  review's later broad Windows snapshot passed1,033/skipped54 before the final
  extra regressions; the final source's Linux runs include all414 statistics tests.
- Engine Ruff lint/format and Pyright passed; root Ruff compatibility was checked
  with its older pinned tool. Mechanical/reference CP checks passed. Only the
  changed changelog's canonical LF document checksum was updated.

Environment: isolated engine CPython3.12.13, NumPy2.4.2, SciPy1.18.1,
Pydantic2.12.5, Ruff0.15.6, Pyright1.1.408; the separate default install uses
CPython3.12.12. SciPy was added from the official project/PyPI release, locked
under the engine manifest only. No product files/environment, canonical DDL,
frozen split assignments, proposal or checklist were changed.

## Remaining work

None for `RL-034` (`#31`): the `H3` three-way decision protocol (`Supported` /
`Not supported` / `Inconclusive`) and `analyze_h3` integration were completed and
verified in commit `5607e92` (`428` passing unit tests in `evaluation/stats/tests`).
Downstream pilot freeze (`RL-100`) and confirmatory test-split execution
(`RL-101`–`RL-105`) remain separate Phase P10 tasks.
