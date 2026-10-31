# ChronaMind V2 — Inference Verification Statistics

**Status:** Normative and binding. V2 was approved as the canonical plan on 2026-10-31 (see `APPROVAL.md`). *Until r4.2 this read "within the V2 proposal (V2 overall: Proposed)".* Replaces the inference-verification prose in
`ARCHITECTURE_V2.md` §7 and `RESEARCH_PROGRAM_V2.md` §3.3–§3.5, and supersedes
`V2-D02` (in part), `V2-D03` (in part), and the `V2-D15` suggestion.
**Written after independently reassessing the user's three concerns.** Two of them
were right, one was right about the problem and wrong about the recommended fix, and
my own V2 got three things wrong. All corrections are recorded in §8.

---

## 1. The problem, stated precisely

An abstraction is a claim of the form:

> For entity/context set `G`, condition `C` predicts outcome `Y` with probability `p`.

The data available to test it is: **few items, one user, correlated over time, partly
censored, with unknown base rates.** That combination breaks most textbook tools:

| Property | Consequence |
|---|---|
| n is small per user (5–50 relevant memories) | Asymptotic tests invalid; CIs are wide; ECE undefined in practice |
| One user, many domains | No between-subject variance to borrow, unless you make a hierarchical assumption |
| Temporally correlated | Items are not exchangeable → naive bootstrap and split-conformal both break |
| Right-censored | Absence of counterevidence is often absence of *opportunity* |
| Base rate unknown and shifting | Precision alone is uninterpretable; need a reference class |
| Repeated testing (scheduler) | Fixed-alpha tests are invalid under continuous monitoring |

**Everything below follows from taking these six facts seriously.**

---

## 2. Counter-support: the three-state evidence ledger

### 2.1 The decision the user challenged, and my reassessment

V2-D03 required a **non-empty counter-set** for every abstraction. That is wrong, and
the user is right to flag it. Three reasons:

1. **It conflates two different things.** "Observed a counterexample" and "had no
   opportunity to observe one" are not the same evidence. Mandating non-empty forces
   the system to treat absence-of-opportunity as counterevidence, or to fabricate.
2. **It is unsatisfiable for genuinely stable preferences.** "Never wants emoji"
   may be true for years. Forcing counterexamples produces either dishonest records
   (the generator will find *something* that looks like a violation) or the refusal
   to abstract anything stable — which destroys the product.
3. **It creates a perverse incentive for the LLM producing candidates.** If the
   admission rule requires counterexamples, a model under pressure will manufacture
   them.

The underlying worry was correct: *a zero-counter abstraction is suspect, because
zero counters often means the condition was never exercised.* The fix is not to
require counters. It is to **model opportunity explicitly.**

### 2.2 The estimand, stated before the estimator

The single most common error in this kind of system is to define the metric after seeing
the data. So the estimand is fixed here.

**Reference population.** For an abstraction `a` with condition `C` and outcome `Y`, the
reference population is the set of **opportunities**: items in `a`'s declared scope and
time window at which `C` held and `Y` was *evaluable*. Items where `C` never held are
not opportunities and belong to the negative-control slice (§3), not the denominator.

**Estimand.** `p_f(a)` = the probability that `Y` fails to occur, **conditional on an
opportunity arising**, over the reference population. This is a
*prediction* estimand, not a causal one. `a` does not claim that `C` causes `Y`; it
claims that `C` predicts `Y`. Any causal reading would need a design we do not have.

**Missing data.** Every opportunity is one of three states:

| State | Meaning | Status in the estimand |
|---|---|---|
| `s` **support** | `C` held, `Y` occurred | observed, not a failure |
| `k` **counter** | `C` held, `Y` did not occur | observed failure |
| `u` **undetermined** | `C` held, outcome not evaluable (censored, ambiguous, unlabelled) | **MISSING, not observed** |

`o = s + k + u`.

**The assumption, stated explicitly.** The bound below is valid under **missing at
random is *not assumed***. Instead we use **partial identification**: `u` is allowed to
be adversarially arranged. This is deliberate. A system that will act on the result
should assume the worst about what it could not see, because the alternative —
assuming `u` are successes — is exactly the error that makes a memory engine
confidently wrong.

**Partial identification interval.** Let `x` be the true number of failures among the
`u` undetermined items, so `x ∈ {0, …, u}`:

```text
p_f ∈ [ k / o ,  (k + u) / o ]                         # point-identified bounds on the proportion
p_f ∈ [ CP_lower(k, o),  CP_upper(k + u, o) ]          # finite-sample bounds (one-sided 97.5%)
confidence_lower = 1 − CP_upper(k + u, o)              # the conservatative, admission-relevant end
```

**This is the correction that matters most in r3.** The r1/r2 rule used
`1 − CP_upper(k, o)`, which treats every undetermined item as a success. That is not a
conservative approximation — it is an unstated and false assumption, and it produces
absurd admissions:

| Case | r1/r2 `confidence_lower` | Corrected `confidence_lower` | Truth |
|---|---|---|---|
| `s=5, k=0, u=45` (`o=50`) | **0.929 → admitted at τ=0.90** | **0.033 → rejected** | Five resolved observations cannot support a claim |
| `s=40, k=0, u=0` (`o=40`) | 0.912 | 0.912 | Genuinely strong |
| `s=30, k=0, u=10` (`o=40`) | 0.912 | **0.588** | Weaker than it looks; correctly rejected |

**Consequences for the ledger fields.**

- `coverage = (s + k) / o` is a **gate**, not a report. Admission requires
  `coverage ≥ τ_coverage` *in addition to* the confidence bound. The two are not
  redundant: coverage bounds how much is unknown, the confidence bound how much of the
  known is failure.
- `confidence` is `1 − CP_upper(k + u, o)`, always. There is no code path that omits
  `u` from the upper bound.
- The **resolved-only rate** `k / (s + k)` and its own interval are reported separately
  and labelled "conditional on resolution." They are diagnostics; they never decide
  admission, because conditioning on resolution discards exactly the information that
  makes the claim uncertain.

### 2.3 What an abstraction stores (revised)

| Field | Type | Note |
|---|---|---|
| `support_count`, `counter_count`, `undetermined_count`, `opportunity_count` | int | `o = s + k + u`, enforced by `CHECK` |
| `coverage` | float | `(s+k)/o`; shown to the user |
| `evidence_generation` | int | How many times the underlying corpus changed since creation; distinguishes "no counter observed" from "not looked again" |
| `counter_set` | rows | May be **empty**, with `counter_absence_basis ∈ {high_opportunity, low_opportunity}` |
| `refutation_attempts` | int | Adversarial search attempts made (§3), recorded even when they fail |
| `confidence` | float + method | Calibrated, §4 |

**V2-D03 is amended:** counter-support is *required to be searched for and recorded*,
not *required to be non-empty*. The admission rule uses `o`, `k`, and `coverage`
(§4), which is what the original rule was reaching for.

---

## 3. Refutation is an active process, not a passive count

If we do not require counters to exist, we must require the system to **go looking**.
This is Popperian in intent and cheap in practice:

| Attempt | Method | Cost |
|---|---|---|
| **Same-scope re-scan** | Run the predicate over all items where `C` held, including ones excluded from generation. | cheap |
| **Near-miss search** | Retrieve items at high embedding similarity to `C`'s context but with the opposite outcome (the classic "counterexample retrieval"). | one embedding query |
| **Scope widening** | Test whether the claim survives outside its scope (it should *not* — this validates the scope rather than the claim). | cheap |
| **Perturbation** | Change one attribute of `C`; if the prediction is unchanged, the abstraction is probably an artifact of a confounded attribute. | cheap, deterministic |
| **Negative-control slice** | A generator-provided slice where `C` never holds; the abstraction must *not* predict there. | free, and it is the strongest single test |

**`refutation_attempts` is recorded and reported.** An abstraction with zero attempted
refutations is not "verified"; it is "untested," and the metrics module says so.

This replaces the vague "held-out verification" language with a concrete, auditable
procedure — and the negative-control slice is the same idea as suite `D`'s
"never-stated" probes and suite `D`'s `A8` positive control, applied to inference.

---

## 4. Admission rule: confidence is a bound, not a point

### 4.1 What was wrong with the ECE admission gate

`ARCHITECTURE_V2.md` §2.2 used "confidence is calibrated (ECE gate)" as an admission
predicate. This does not work, for four independent reasons:

1. **ECE is a batch statistic, not a per-item one.** It measures the calibration of a
   *set* of predictions. You cannot decide whether a single abstraction is admissible
   from a set-level average — and with n = 12, the set is too small to estimate it.
2. **ECE is binning-dependent and noisy at small n.** With 3–10 bins and 12 items, most
   bins are empty; ECE is dominated by the binning choice. Published ECE numbers for
   small samples are close to uninterpretable.
3. **Circularity.** Admission requires calibration; calibration requires labelled
   verification outcomes; verification requires the abstraction to be admitted first
   (you must store it to test it against held-out data). The V2 text papered over this
   with the "verified later" path, which makes ECE an admission rule for a *second*
   stage that was never specified.
4. **It is trivially gamed.** An operator that emits confidence `0.5` always has low
   ECE and zero information. ECE as a gate rewards uninformative models.

### 4.2 The replacement: a finite-sample upper bound on the failure rate

Every abstraction's confidence is reported as a **two-sided interval on the failure
rate**, computed from `(k, o)` with the finite-sample method already in the repo
(`evaluation/stats/clopper_pearson.py`, verified against `validation/cp_bounds.output.txt`):

```text
failure_rate p_f  ∈  [ CP_lower(k, o),  CP_upper(k + u, o) ]   # finite-sample, partially identified
confidence_lower  =  1 − CP_upper(k + u, o)
```

**One-sided convention, fixed for the whole system.** All bounds use a **one-sided 97.5%
limit**, which is what the repository's `cp_upper` computes with its default
`alpha = 0.05` (it solves `P(X ≤ k | n, p) = 0.025`, i.e. a two-sided 95% interval, whose
upper end is a one-sided 97.5% limit). Inference and the v1 deletion guarantee therefore
share **one** convention. Mixing one-sided 95% and two-sided 95% would move the
zero-counter threshold from `o ≥ 36` to `o ≥ 29` and would let the two claims be
compared as if they were equally strong when they are not (§7).

Admission then uses **closed testing** (a valid multiple-testing procedure that
composes with the existing Holm machinery):

```text
admissible(abstraction) ⟺
      confidence_lower ≥ τ_confidence          # e.g. 0.90
  AND coverage ≥ τ_coverage                    # e.g. 0.60, kills censoring-heavy claims
  AND refutation_attempts ≥ 3                  # §3
  AND k > 0 OR CP_upper(0, o) ≤ 1 − τ_confidence   # zero-counter is OK only with o large enough
  AND negative_control_slice_clean             # §3
```

**The zero-counter case is now handled honestly**, and the numbers are reproducible
(`scripts/check_statistics.py`, §10). With `k = 0, u = 0`:

| `o` | `CP_upper(0, o)` at one-sided 97.5% | Admissible at `τ = 0.90`? | Rendered as |
|---|---|---|---|
| 8 | 0.3694 | **No** (`confidence_lower = 0.631`) | hypothesis only |
| 29 | 0.0981 | no (marginal; fails at 97.5%) | hypothesis only |
| **36** | 0.0974 | **yes** (bound just under 0.10) | "≤ 9.7% failure rate at 97.5% confidence" |
| 40 | 0.0881 | yes | "≤ 8.8% at 97.5%" |
| 100 | 0.0362 | yes | "≤ 3.6% at 97.5%" |

The r2 draft quoted `≈ 0.32` for `o = 8`; that is the **one-sided 95%** value
(`1 − 0.05^{1/8} = 0.3123`), not the system convention. The correct figure is
**0.3694**. This is precisely the inconsistency §4.2 now eliminates, and it is why the
document carries a checked numerical table instead of prose approximations.

That is exactly the `H4`/deletion-bound logic, reused. It is the single most important
consistency win of the revision: **the project already had the right instrument for
"zero observed failures" and was not using it for inference.**

**Why this is better than ECE:**
- per-item and finite-sample valid;
- no binning, no circularity, no gaming incentive (tight bound requires data);
- consistent with the deletion guarantee's epistemology;
- one-sided and conservative, which is the right direction for a system that will act
  on the result.

### 4.3 ECE is retained — but as a *monitoring* metric, not an admission gate

ECE (with reliability diagrams and Brier score) stays in the metrics module
(`C-013`) because it answers a different and real question: *is the stated confidence
of the whole store drifting?* It triggers recalibration and operator review. It never
decides admission. This resolves `V2-D15`.

---

## 5. Conformal methods: valid, but only in the right variant

The user asked whether conformal methods are appropriate. Answer: **yes for interval
predictions, no in the naive form, and it is the wrong tool for the binary admission
decision.**

| Question | Verdict |
|---|---|
| Split conformal for "is this abstraction's prediction within tolerance on a new item"? | **Valid only under exchangeability**, which temporal user data violates. |
| Can we fix that? | **Yes** — blocked/adaptive conformal (§5.1), which restores a *long-run* coverage guarantee under drift. |
| Is conformal the right tool for "admit / don't admit this abstraction"? | **No.** Conformal gives prediction sets with coverage, not per-hypothesis error control. Use CP bounds + closed testing (§4.2). |
| Does conformal help the *user-model* (F4) case? | **Yes, strongly** — see §5.2, where hierarchical borrowing is the actual requirement. |

### 5.1 Where conformal is used: temporally valid coverage

For continuous outcomes (e.g., predicted session length, predicted response length
preference), split-conformal with a random split breaks under temporal dependence.
Use:

- **Blocked calibration:** calibration items are contiguous time-blocks, not random
  items. Blocks treat within-block correlation as one unit, which is exactly the
  cluster-bootstrap logic already in `evaluation/stats/bootstrap.py`.
- **Adaptive Conformal Inference (ACI):** update the miscoverage level `α_t` online by
  `α_{t+1} = α_t + γ(α − err_t)`. This gives a long-run coverage guarantee *without*
  exchangeability — the correct guarantee for a drifting user.
- **Weighted conformal** for covariate shift across facets (a preference's calibration
  may transfer between recommendation contexts but not between decision contexts).

### 5.2 The sparse-user problem: hierarchy, not a bigger test

The deepest issue the user raised is not "which test" but "**there is not enough data
about this one person.**" No single-user method fixes that; the fix is structural:

> **Per-user estimates are shrunk toward a population prior over users, with partial
> pooling, and the amount of shrinkage is itself estimated from the data.**

Concretely, for each facet, fit a **hierarchical beta-binomial** (preference exists /
does not, per opportunity):

```text
p_i ~ Beta(μ_facet · κ_facet, (1 − μ_facet) · κ_facet)      # user i's rate
k_i | o_i, p_i ~ Binomial(o_i, p_i)                          # observed
```

- `μ_facet`, `κ_facet` are learned across users within the profile (the *only*
  legitimate use of cross-user data, and it is aggregate, never per-item).
- A new user with `o = 3` inherits the facet prior: the posterior is wide and
  **honestly** so — which means few user-model claims are admissible early. That is
  the correct behaviour, and it is what "cold start" should feel like.
- As evidence accumulates, shrinkage fades automatically. No threshold tuning.

**This is the answer to "statistically valid for sparse, user-specific,
temporally correlated data":** exact finite-sample bounds for the decision, hierarchy
for the sparsity, temporal blocking for the correlation, and calibration as monitoring.

### 5.3 Repeated looks: always-valid inference

The reflection scheduler re-evaluates abstractions continuously. Fixed-α tests would
inflate false positives arbitrarily. Two acceptable remedies, both cheap:

- **Alpha-spending** over a declared number of looks (simple, conservative);
- **Confidence sequences** (always-valid, no pre-declared number of looks) for the
  continuous-monitoring case.

V2 mandates one of them (decision `V2-D20`), because "we re-check every night" plus
"we use p < 0.05" is exactly the multiplicity error the v1 protocol was built to avoid.

---

### 5.4 Units, assumptions, and what each guarantee actually buys

Every method above rests on assumptions. This section states them, because claiming a
method is "exact" without them is the error the review asked us to avoid.

#### 5.4.1 Units of analysis

| Analysis | Independent unit (cluster) | Observation | Why not the item |
|---|---|---|---|
| Abstraction support/failure | **declared time-block** within scope (default: calendar week; configurable) | one opportunity | Consecutive opportunities share context; treating each as independent overstates `n` |
| User-model facet rate | **user** | one opportunity within that user | The estimand is per-user; pooling is a prior, not a sample |
| Pooling hyper-parameters | user (facet level) | user-level `(k, o)` pair | Aggregate only; never per-item transfer |
| Verification comparison | **scenario** (as in v1 `T`/`G`/`D`) | one prediction | Keeps `S9`–`S13` comparable to v1's cluster-bootstrap conventions |
| Deletion leakage | **scenario** (unchanged from v1) | one probe set | |

#### 5.4.2 Guarantee table (what is claimed, under what assumption)

| Method | Guarantee | Assumptions | If violated | Honest label |
|---|---|---|---|---|
| Clopper–Pearson bound with `u ≤ o` | Confidence level at least `1 − α` on the partial-identification upper limit | Each opportunity's outcome is a Bernoulli draw; observations **exchangeable**; `u` at worst adversarially arranged (worst case, no assumption) | Positive autocorrelation makes `CP` **anti-conservative**: the true upper limit is higher than reported | "finite-sample, valid under exchangeability" — never "exact" unqualified |
| Design-effect correction `n_eff = n / (1 + (m−1)ρ)` | Nominal level if `ρ` is a **conservative over-estimate** | An intra-class correlation model; `m` = average block size; `ρ` estimated from the data (upper CI used) | Under-estimated `ρ` → anti-conservative | "approximately valid; conservative if `ρ` over-estimated" |
| **Block bootstrap** on time-blocks | Percentile interval with asymptotic validity under **stationary** block-dependence | Blocks longer than the correlation length; enough blocks (`≥ 10–20`) | Too few blocks → miscalibrated; v1's own `B = 10_000` convention applies | "asymptotically valid under stationarity" |
| Hierarchical beta-binomial pooling | **Credible** interval under the assumed prior family | `Beta(μ_facet·κ, (1−μ)·κ)` with `μ, κ` estimated across users; exchangeable users within a facet | Prior mis-specification biases the estimate, most for the sparsest users | "bayesian credible" — never "confidence" |
| Always-valid confidence sequence | Time-uniform error control over **any** stopping rule | A valid betting/`e`-process construction for the Bernoulli model; blocks used as the unit | Wrong construction → guarantee void | "time-uniform, no pre-declared look count" |
| Adaptive Conformal Inference | Long-run (not finite-time) coverage under drift | Step size `γ` chosen; no exchangeability required | Small `γ` → slow adaptation; large `γ` → wide intervals | "long-run coverage under drift" |
| Closed testing + Holm | FWER across candidates | Each local test valid at its level; monotonicity | Local tests invalid ⇒ FWER uncontrolled | depends on local test |

**The word "exact" is now reserved for the single case where it is literally true:**
Clopper–Pearson at fixed `n` under exchangeability. Every other method carries its
qualifier. `ARCHITECTURE_V2.md` and `DECISIONS_V2.md` are aligned with this table.

#### 5.4.3 Composition of the error controls

Three quantities are controlled, and they are not interchangeable:

| Dimension | Controlled by | Level | Scope of the guarantee |
|---|---|---|---|
| Per-abstraction confidence | CP bound with `u` in the numerator | one-sided 97.5% | one abstraction, one reference window |
| Across candidates | closed testing + Holm | FWER 0.05 | all candidates tested in a reflection run |
| Across repeated looks | confidence sequence | time-uniform | the whole monitoring lifetime of the abstraction |

**The order matters and is specified:** admission is a three-condition test
(per-abstraction bound ∧ family-wise significance ∧ time-uniform validity at the
current look). Failing any one demotes to hypothesis. Because the family-wise and
time-uniform controls are both conservative, their conjunction is conservative too —
which is the right direction for a system that acts on its conclusions.

#### 5.4.4 The honest limit

With thousands of candidate groupings per user and 5–50 opportunities per user, **no
method can give tight per-abstraction guarantees**. The design response is not to
pretend otherwise; it is to (a) use conservative bounds, (b) keep unverified artifacts
in the hypothesis channel where they cannot masquerade as facts (`I1′`), (c) measure the
realized `FAR_τ` (`STATISTICS.md` §7) and report it, and (d) let the product work with
wide intervals rather than narrow lies.

---

## 6. Held-out verification: what it must actually be

The user asked to distinguish "actual counterevidence" from "absence of observed
counterevidence." Applied to held-out verification, the requirement becomes:

| V2 said | Problem | Now |
|---|---|---|
| "verified on a held-out split disjoint from support" | Disjoint *items* is not enough if they come from the same time period and same context (they are near-duplicates). | Disjointness must be **temporal and contextual**: the verification slice is later in time and covers contexts the support set did not. |
| "predicts held-out behaviour better than chance-at-its-support-rate" | "Chance" is unspecified, and with small n the comparison has no power. | Compare against **two** baselines: (a) the **base-rate predictor** (predict the majority outcome in the verification slice), and (b) the **prevalence-matched null** (a permuted-label control that preserves prevalence and autocorrelation). |
| — | Nothing checked that the *scope* was real. | **Scope validation**: the abstraction must fail the negative-control slice. An abstraction that predicts everywhere has learned nothing. |

The **permuted-label null with block permutation** (not iid permutation) is the key
addition: it preserves the temporal autocorrelation that makes naive permutation tests
anti-conservative — the exact issue v1's sign-flip test handled by clustering.

---

## 7. What this means for the metrics (unchanged names, changed internals)

| Metric | Change |
|---|---|
| `precision@verified` | Now reported with a CP interval and the base-rate baseline alongside. Precision without a base rate is uninterpretable (a 0.8 precision on a 0.6-prevalence task is nearly worthless). |
| `false-abstraction rate at conf > 0.7` | Replaced by **`FAR_τ`** = rate of *abstractions* admitted with `confidence_lower ≥ τ` that are in fact false. Directly meaningful, and directly comparable to the deletion bound. |
| calibration (ECE) | Demoted to monitoring; **Brier score + reliability diagram** reported per facet, not aggregated. |
| counter-support completeness | Replaced by **`coverage`** and **`refutation_attempts`**, both reported per abstraction. |
| refutability | Operationalised as: exists a generated counter-world that flips the verification outcome (test, not prose). |

---

## 8. Corrections to my own V2 (recorded, not hidden)

| Item | V2 said | Status | Why |
|---|---|---|---|
| `V2-D03` mandatory non-empty counter-set | required | **AMENDED** (§2.2) | Conflated absence-of-opportunity with counterevidence; unsatisfiable for stable preferences; incentivised fabrication. |
| `ARCHITECTURE_V2.md` §2.2 "ECE gate" | admission requirement | **REPLACED** (§4.2) | ECE is a batch statistic, circular as an admission rule, and gameable. |
| `ARCHITECTURE_V2.md` §8 "split-conformal check" | verification method | **REPLACED** (§5) | Split conformal assumes exchangeability, which time-ordered user data violates. |
| `V2-D15` (conformal vs Platt vs isotonic) | open decision | **RESOLVED** (§4.3, §5) | Different tools for different questions; conformal is for intervals, CP bounds are for admission. |
| `RESEARCH_PROGRAM_V2.md` §3.3 held-out verification | item-disjoint only | **STRENGTHENED** (§6) | Item disjointness admits near-duplicate leakage. |
| **r3:** `1 − CP_upper(k, o)` used `u` as success | admission bound | **CRITICAL FIX** (§2.2, §4.2) | Unknown observations were treated as successes. `s=5,k=0,u=45` was admitted at τ=0.90; correct bound is 0.033. |
| **r3:** one-sided 95% and two-sided 95% mixed | convention | **FIXED** (§4.2) | Moved the zero-counter threshold from 36 to 29; made inference and deletion incommensurable. |
| **r3:** "exact" claimed for CP under temporal correlation | guarantee wording | **FIXED** (§5.4) | CP is only exchangeability-exact; under autocorrelation it is anti-conservative. Qualifiers now mandatory. |

The user's three concerns: **counter-support → right problem, wrong fix** (opportunity
ledger instead of mandatory counters); **ECE → correct diagnosis** (removed as gate);
**conformal → right instinct, must be the block/adaptive variant, and it is not the
admission tool**. Net: two of my decisions were genuinely wrong, and the third needed
specification rather than reversal.

---

## 9. Decisions produced by this document

| ID | Decision |
|---|---|
| `V2-D18` | Three-state evidence ledger (`support`/`counter`/`undetermined`/`opportunity`); counter-set may be empty; `coverage` reported. |
| `V2-D19` | Admission via finite-sample CP upper bound on the failure rate, with `u` counted as failure in the upper bound + closed testing; ECE demoted to monitoring. |
| `V2-D20` | Repeated looks use alpha-spending or always-valid confidence sequences; fixed-α recomputation is forbidden. |
| `V2-D21` | Held-out verification requires temporal **and** contextual disjointness, a base-rate baseline, a block-permutation null, and a clean negative-control slice. |
| `V2-D22` | Sparse users are handled by hierarchical partial pooling per facet; cross-user data is aggregate only, never per-item. |
| `V2-D23` | Conformal is used only in blocked/adaptive/weighted form, and only for interval prediction — never for admission. |

---

## 10. Reproducible numerical validation

Every number quoted in this document is produced by one script, and the script is part of
the reproducibility pack (`R-031`). Required before `SD-002` is accepted:

```python
# scripts/check_statistics.py — run: uv run python scripts/check_statistics.py
from math import ceil, log
from validation.cp_bounds import cp_upper          # existing, unchanged v1 asset
from scipy.stats import beta                       # CP_lower

ALPHA = 0.05                                        # two-sided 95% == one-sided 97.5%
TAU_CONF, TAU_COV = 0.90, 0.60

def ci(a):
    s, k, u = a["s"], a["k"], a["u"]; o = s + k + u
    assert o > 0, "opportunity_count must be positive"
    lower_rate = k / o if o else 0.0
    upper_rate = (k + u) / o if o else 1.0
    cp_hi = cp_upper(k + u, o)                      # u counted as failure  <-- r3 fix
    return dict(o=o, coverage=(s + k) / o,
                conf_lower=1 - cp_hi,
                rate_bounds=(lower_rate, upper_rate),
                cp_bounds=(beta.ppf(ALPHA / 2, k, o - k + 1) if k else 0.0, cp_hi))

CASES = {
    # r3 regression: unknown was being used as success
    "sparse_mostly_unknown": dict(s=5,  k=0, u=45),
    "all_resolved":          dict(s=40, k=0, u=0),
    "some_unknown":          dict(s=30, k=0, u=10),
    "small_n":               dict(s=8,  k=0, u=0),
    "with_counters":         dict(s=30, k=2, u=8),
}

for name, a in CASES.items():
    r = ci(a)
    admitted = (r["conf_lower"] >= TAU_CONF and r["coverage"] >= TAU_COV
                and (a["k"] > 0 or cp_upper(0, r["o"]) <= 1 - TAU_CONF))   # see §4.2
    print(f"{name:24s} o={r['o']:3d} cov={r['coverage']:.2f} "
          f"conf_lower={r['conf_lower']:.4f} admitted={admitted}")

# zero-counter threshold, both conventions (must be 36 under our convention)
for label, alpha in (("one-sided 95%", 0.05), ("one-sided 97.5% (ours)", 0.025)):
    n = ceil(log(alpha) / log(TAU_CONF))
    print(f"zero-counter threshold, {label}: o >= {n}  (bound {1 - alpha ** (1 / n):.4f})")

# design-effect sensitivity: 40 opportunities, clustering penalty
for rho, m in ((0.0, 1), (0.05, 4), (0.20, 4), (0.20, 8)):
    neff = int(round(40 / (1 + (m - 1) * rho)))
    print(f"rho={rho:<5} m={m}: n_eff={neff:3d} CP_upper(0,n_eff)={cp_upper(0, neff):.4f}")
```

**Assertions the script must satisfy (these are the acceptance criteria for `SD-002`):**

| Assertion | Expected |
|---|---|
| `sparse_mostly_unknown` is **not** admitted | `conf_lower ≈ 0.033 < 0.90` — this is the r3 regression test |
| `all_resolved` **is** admitted | `conf_lower ≈ 0.9119` |
| `some_unknown` is **not** admitted | `conf_lower ≈ 0.588` |
| `small_n` (`o=8`) is **not** admitted | `0.6306 < 0.90` |
| zero-counter threshold under our convention | `o ≥ 36` |
| `cp_upper(0, 36)` | `≈ 0.0974`, matching `validation/cp_bounds.output.txt` |
| `cp_upper(0, n)` agrees to `1e-9` with `1 − 0.025^(1/n)` | true (v1 asset, unchanged) |
| `ci(all_resolved)["cp_bounds"]` reproduces `cp_upper(0,40)` | `0.0881` |

The last two assertions are what tie the new inference statistics to the **existing,
verified** v1 Clopper–Pearson implementation rather than to a reimplementation.
