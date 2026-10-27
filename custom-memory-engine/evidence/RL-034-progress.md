# RL-034 software implementation status — 27 October 2026

**In progress.** Numerical primitives and H1/H2/H4 decisions are implemented.
The user is asking the leader to clarify H3's undefined crossing-zero ASR cases;
the final H3 verdict evaluator is deliberately pending. This is neither a
STAT-UNIT completion claim nor a pilot/confirmatory research result.

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
- Default install: 975 passed, 56 expected optional-package/service skips. All six
  optional model/vendor packages were confirmed absent. This broad run preceded
  the last 13 persisted-summary regression additions; latest focused checks cover
  those changes. Exact submitted-head CI remains to be inspected.
- Full-service verification is in progress; its result and owned-resource cleanup
  must be recorded before claiming that check passed.
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
decision, reconcile any resulting documentation, finish full-service/default
and exact-head CI verification, and complete independent final review. Leader
review/merge and the project's research approval/freeze gates remain separate.
