# Paired statistical inference (RL-034)

This library implements software arithmetic for Proposal §§4.1/10.6. It does not
run experiments, access held-out data, choose pilot thresholds, establish judge
validity, or provide research approval. Examples and tests use synthetic scores.

## Data and estimands

`models.Observation` identifies one fraction-valued metric by cluster, item and
repeated-run ID. `pair_observations` joins P and its comparator on all three
identities, refusing duplicates, missing counterparts and incomplete run grids.
There is no inner-join dropping or missing-score imputation. `PairedSample`
contains one dataset, endpoint and comparator; datasets and H1 strata are separate.

Repeated runs are averaged within each question/event first. Item differences
are then averaged within their scenario/conversation. The reported effect is
the **equal-weight mean of cluster differences**, regardless of question count.
LongMemEval_S questions must each be their own cluster. LoCoMo conversations
remain clusters, and their results are descriptive only. Inputs are already
scored fractions in [0,1]; null/unavailable metrics must be resolved by the
predeclared analysis population, never converted to zero by this library.

`analysis.analyze_contrast` returns the paired data, its canonical SHA-256,
bootstrap intervals and, for superiority endpoints, the sign-flip p-value and
positive/zero/negative cluster counts. Negative differences mean better ASR;
positive differences mean better TAA/recall/LUAR. NI endpoints and LoCoMo return
no extra hypothesis-test p-value. Per-cluster effects supply the reporting plot
data. Persisted summaries validate their hashes, cluster effects, counts, seeds,
interval levels and NI bounds to reject accidental mixing of analysis sources.

## Numerical methods

- `bootstrap.cluster_bootstrap` resamples entire paired clusters with replacement,
  at least 10,000 times, using an explicit seed and local NumPy PCG64 generator.
  It does not resample items or runs independently. Percentiles use explicit
  linear interpolation. It returns 95% and conservative `(1 − 0.05/3)` intervals,
  plus the one-sided 97.5% lower bound (the same 2.5th percentile as the 95% lower
  endpoint). Temporary resampling matrices are bounded by batches.
- `permutation.paired_permutation` tests the absolute mean paired difference,
  two-sided. Exactly zero clusters remain in the effect/counts but do not enlarge
  the permutation space. At most 16 nonzero clusters are enumerated by default
  (configurable 0–20); larger inputs use fixed-seed Monte Carlo and the
  conservative `(extreme + 1)/(draws + 1)` estimate. Floating comparisons near a
  tie fall back to the exact integer lattice of the binary input values.
- `multiplicity.confirmatory_holm` uses exactly three inputs to the global family:
  H1 history, `max(H2_dense, H2_lexical)`, and H3 ASR. Holm adjustment is across
  these three tests. The H2 max-p IUT and the two NI conditions need no additional
  within-hypothesis correction. Exploratory baselines never enter this family.
  `family.evaluate_family` assembles the six endpoint analyses with explicit
  caller-supplied plan parameters, and refuses an incomplete superiority family.
- `clopper_pearson.cp_upper` uses a 0.025 tail by default: the upper endpoint of
  the two-sided 95% interval, equivalently a one-sided 97.5% upper bound. With
  zero leaks it uses stable `−expm1(log(0.025)/N)`. `cp_interval` also supplies the
  lower endpoint and supports nonzero counts. Independent scenarios are the
  H4 denominator; deletion events and channels do not multiply N. The same
  numerical primitive can bound Suite I independent exposure-trial counts;
  their independence must be justified by the caller's protocol.

These conventions follow the frozen proposal. Reference implementations and
numerical behavior were checked against the official
[SciPy exact-binomial documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html)
and [paired permutation documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html).
SciPy 1.18.1 is pinned only in the independent engine environment; the root
application environment is unchanged.

## Decisions and pending clarification

H1 combines history superiority with present-state NI. Support uses strict
`adjusted_p < .05`, history conservative lower `> 0`, and present NI lower
`> −margin`. Rejection uses history upper `<= 0` or present 95% upper `< −margin`.
H2 uses both positive point estimates, both conservative intervals above zero,
and globally adjusted max-p `< .05`. Proposal §4.1's inconclusive cell explicitly
covers an interval containing zero; this gate reconciles the whole row and is
also explicit in Technical Specification §9.3. A contrast upper `<= 0` rejects.
For superiority rejection, “the interval” is taken to mean the conservative
interval used for confirmatory superiority; this interpretation is explicit for
review. All remaining nonsupport cases are inconclusive.

The arithmetic feasibility floor is **seven nonzero independent clusters** for
each superiority contrast; the H1 floor is on history-dependent scenarios.
Insufficient clusters or an explicit inadequate pilot precision assessment
produce an inconclusive decision before superiority/NI classification. This is
not a power claim. Margins, precision assessments, H4 informativeness/control
thresholds, frozen preconditions and the freeze reference are required arguments.
No provisional pilot values are silently promoted to frozen thresholds.

H4 is supported only with zero observed leaks, scenario CP upper `<= u*`, control
detection share `>=` its frozen threshold, and all frozen preconditions met. Any
confirmed in-boundary leak takes precedence and rejects; otherwise failure of
the bound/control/precondition is inconclusive. This never proves impossibility
of leakage or covers unprobed/out-of-boundary channels.

**Pending leader clarification:** Proposal §4.1 does not completely define H3
cases where ASR's interval spans zero. The user is asking the leader for the
intended rule. `h3_conditions` exposes the already specified benefit/NI conditions
without emitting a final verdict. The full H3 decision evaluator and RL-034
completion remain pending that answer; no inferred rule is frozen here.

## Synthetic use

```python
from evaluation.stats.analysis import analyze_contrast
from evaluation.stats.models import Observation, pair_observations

p = [Observation(cluster_id=f"s{i}", item_id=f"q{i}", run_id="r0", value=1.0) for i in range(7)]
a2 = [Observation(cluster_id=f"s{i}", item_id=f"q{i}", run_id="r0", value=0.0) for i in range(7)]
paired = pair_observations(p, a2, dataset="T", endpoint="history_taa", comparator_id="A2")
evidence = analyze_contrast(paired, seed=42)
assert evidence.bootstrap.estimate == 1.0
assert evidence.permutation.p_value == 2 / 2**7
```

Run software checks from `custom-memory-engine/` with
`uv run --frozen pytest evaluation/stats/tests -q`. No research dataset or provider
credential is needed.
