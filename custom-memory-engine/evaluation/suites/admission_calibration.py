"""Admission calibration: the §4.2 predicate's false-admission rate under known generative processes.

**What this is not.** It is not gate `G4` and it is not task `C-012`. `C-012`'s null suite measures *confabulation
in the engine*: no-pattern event streams driven through `infer` and `verify`, so that the acceptance rate of stored
abstractions is observed rather than assumed. That instrument does not exist yet and is W4 work
(`ROADMAP_V2.md` §7). This module never calls the engine.

**What it is.** A self-check of the admission rule itself, in the sense in which a statistical procedure can be
checked against its own generative model. It draws `(s, k, u)` from Bernoulli trials with a known true support
probability and feeds them to `evaluation.stats.admission.assess_admission`, then reports whether:

1. **The size is not exceeded.** Across stratified null processes (`p_true <= tau_confidence`: spurious 0.50,
   base-rate 0.75, the boundary 0.90, high censoring, and the small-sample `o < 36` trap), the scenario-level
   false-admission rate stays at or below `MAX_NULL_FAR`, **per stratum as well as pooled**, each with its own
   Clopper–Pearson upper bound reported beside it. The boundary stratum is where Clopper–Pearson's coverage guarantee
   does the work, so its near-zero count is a property of the bound rather than an empirical discovery; the
   informative strata are the censoring and small-sample ones, where coverage or the zero-counter rule could fail.
2. **The controls separate.** Under clustered null evidence the naive independence assumption exceeds
   `MAX_NULL_FAR` while the `SD-008` design-effect correction keeps it below, so the correction is shown to be
   load-bearing rather than decorative. The `r2` (`X-ignore-undetermined`) control admits and the `r3` form rejects,
   so treating undetermined outcomes as failures is shown to buy its conservatism.
3. **It is not merely conservative.** With a planted signal (`p_true = 0.99`, `o >= 36`) admission clears, so a rule
   that never admits cannot pass this study.

The verdict is a function of the drawn counts at one pinned seed, and the report records the seed for that reason: it
is a calibration check, not a gate whose level is a claim about the engine.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import Field

from evaluation.stats.abstraction_metrics import TAIL_PROBABILITY
from evaluation.stats.admission import (
    DEFAULT_TAU_CONFIDENCE,
    DEFAULT_TAU_COVERAGE,
    MIN_REFUTATION_ATTEMPTS,
    assess_admission,
    compute_far_tau,
)
from evaluation.stats.clopper_pearson import cp_upper
from evaluation.stats.dependence import ClusterObservation, assess_dependence
from evaluation.stats.models import StatsRecord

MAX_NULL_FAR = 0.05


class StratumResult(StatsRecord):
    """Summary of one stratified null or signal cohort."""

    name: str = Field(min_length=1)
    regime: Literal["null", "clustered_null", "planted_signal", "r2_regression_control"]
    scenarios: int = Field(ge=1)
    admitted: int = Field(ge=0)
    admission_rate: float = Field(ge=0.0, le=1.0)
    cp_upper: float = Field(ge=0.0, le=1.0)
    naive_clustered_admission_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class AdmissionCalibrationReport(StatsRecord):
    """One calibration run across the null, clustered-null, planted-signal and control cohorts."""

    seed: int = Field(ge=0)
    tau_confidence: float = Field(gt=0.0, lt=1.0)
    tau_coverage: float = Field(gt=0.0, lt=1.0)
    null_scenarios: int = Field(ge=1)
    null_false_admissions: int = Field(ge=0)
    null_far_tau: float = Field(ge=0.0, le=1.0)
    null_far_cp_upper: float = Field(ge=0.0, le=1.0)
    clustered_naive_far: float = Field(ge=0.0, le=1.0)
    clustered_adjusted_far: float = Field(ge=0.0, le=1.0)
    planted_signal_admission_rate: float = Field(ge=0.0, le=1.0)
    r2_baseline_false_admission_rate: float = Field(ge=0.0, le=1.0)
    strata: tuple[StratumResult, ...] = Field(min_length=1)
    #: The largest per-stratum admission rate over the null strata, which is what the gate tests: pooling alone would
    #: let one failing stratum hide among strata that never admit.
    worst_stratum_admission_rate: float = Field(ge=0.0, le=1.0)
    #: The same stratum's Clopper–Pearson upper bound, reported and not gated: at the study's 20 scenarios a perfect
    #: zero still bounds at 0.168, so a gated bound would need 72 scenarios per stratum and is a W4 decision.
    worst_stratum_cp_upper: float = Field(ge=0.0, le=1.0)
    calibration_passed: bool


def _simulate_iid_stratum(
    rng: np.random.Generator,
    *,
    name: str,
    regime: Literal["null", "planted_signal"],
    scenarios: int,
    p_true: float,
    opportunity: int,
    undetermined_prob: float = 0.0,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
) -> StratumResult:
    admitted = 0
    for _ in range(scenarios):
        is_undetermined = rng.random(opportunity) < undetermined_prob
        is_support = (~is_undetermined) & (rng.random(opportunity) < p_true)
        u = int(is_undetermined.sum())
        s = int(is_support.sum())
        k = opportunity - u - s
        result = assess_admission(
            s,
            k,
            u,
            refutation_attempts=MIN_REFUTATION_ATTEMPTS,
            negative_control_clean=True,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
        )
        if result.admissible:
            admitted += 1
    return StratumResult(
        name=name,
        regime=regime,
        scenarios=scenarios,
        admitted=admitted,
        admission_rate=admitted / scenarios,
        cp_upper=cp_upper(admitted, scenarios, tail_probability=TAIL_PROBABILITY),
    )


def _simulate_clustered_null_stratum(
    rng: np.random.Generator,
    *,
    scenarios: int,
    n_clusters: int = 10,
    cluster_size: int = 5,
    p_cluster_true: float = 0.85,
    within_cluster_flip: float = 0.02,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
    bootstrap_resamples: int = 10_000,
) -> StratumResult:
    """Clustered null where items inside each session share a latent outcome (high intra-class correlation)."""
    naive_admitted = 0
    adjusted_admitted = 0
    for idx in range(scenarios):
        observations: list[ClusterObservation] = []
        s = 0
        k = 0
        for c in range(n_clusters):
            session_supports = bool(rng.random() < p_cluster_true)
            for _j in range(cluster_size):
                item_supports = session_supports if rng.random() >= within_cluster_flip else not session_supports
                if item_supports:
                    s += 1
                    observations.append(ClusterObservation(cluster_id=f"c-{c}", outcome="support"))
                else:
                    k += 1
                    observations.append(ClusterObservation(cluster_id=f"c-{c}", outcome="counter"))
        naive = assess_admission(
            s,
            k,
            0,
            refutation_attempts=MIN_REFUTATION_ATTEMPTS,
            negative_control_clean=True,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
        )
        if naive.admissible:
            naive_admitted += 1

        dep = assess_dependence(observations, seed=idx, resamples=bootstrap_resamples)
        adjusted = assess_admission(
            s,
            k,
            0,
            refutation_attempts=MIN_REFUTATION_ATTEMPTS,
            negative_control_clean=True,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
            effective_opportunity=dep.effective_sample_size,
        )
        # Enforce the most conservative bound across cluster/design-effect/block-bootstrap.
        if adjusted.admissible and dep.conservative_confidence_lower >= tau_confidence:
            adjusted_admitted += 1

    return StratumResult(
        name="clustered_null_rho_high",
        regime="clustered_null",
        scenarios=scenarios,
        admitted=adjusted_admitted,
        admission_rate=adjusted_admitted / scenarios,
        cp_upper=cp_upper(adjusted_admitted, scenarios, tail_probability=TAIL_PROBABILITY),
        naive_clustered_admission_rate=naive_admitted / scenarios,
    )


def _simulate_r2_regression_control(
    *,
    scenarios: int,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
) -> tuple[StratumResult, float]:
    """Compare corrected admission vs superseded r2 formula ``1 - CP_upper(k, o)`` on ``s=5, k=0, u=45``."""
    s, k, u = 5, 0, 45
    o = s + k + u
    r2_admitted = 0
    r3_admitted = 0
    r2_conf = 1.0 - cp_upper(k, o, tail_probability=TAIL_PROBABILITY)
    for _ in range(scenarios):
        if r2_conf >= tau_confidence:
            r2_admitted += 1
        # The control isolates the confidence bound: every other gate is satisfied, so 0 attempts cannot reject it.
        if assess_admission(
            s,
            k,
            u,
            refutation_attempts=MIN_REFUTATION_ATTEMPTS,
            negative_control_clean=True,
            tau_confidence=tau_confidence,
        ).admissible:
            r3_admitted += 1
    return (
        StratumResult(
            name="r3_censoring_regression_guard",
            regime="r2_regression_control",
            scenarios=scenarios,
            admitted=r3_admitted,
            admission_rate=r3_admitted / scenarios,
            cp_upper=cp_upper(r3_admitted, scenarios, tail_probability=TAIL_PROBABILITY),
        ),
        r2_admitted / scenarios,
    )


def run_admission_calibration(
    *,
    seed: int = 20261125,
    scenarios_per_stratum: int = 120,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
    bootstrap_resamples: int = 10_000,
) -> AdmissionCalibrationReport:
    """Run the complete deterministic Gate G4 synthetic null + planted-signal suite."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("nonnegative_integer_seed_required")
    if (
        isinstance(scenarios_per_stratum, bool)
        or not isinstance(scenarios_per_stratum, int)
        or scenarios_per_stratum < 20
    ):
        raise ValueError("at_least_20_scenarios_per_stratum_required")

    rng = np.random.Generator(np.random.PCG64(seed))

    iid_nulls = (
        _simulate_iid_stratum(
            rng,
            name="null_coin_flip_p050",
            regime="null",
            scenarios=scenarios_per_stratum,
            p_true=0.50,
            opportunity=40,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
        ),
        _simulate_iid_stratum(
            rng,
            name="null_spurious_base_rate_p075",
            regime="null",
            scenarios=scenarios_per_stratum,
            p_true=0.75,
            opportunity=45,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
        ),
        _simulate_iid_stratum(
            rng,
            name="null_boundary_p090",
            regime="null",
            scenarios=scenarios_per_stratum,
            p_true=0.90,
            opportunity=40,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
        ),
        _simulate_iid_stratum(
            rng,
            name="null_heavy_censoring_u060",
            regime="null",
            scenarios=scenarios_per_stratum,
            p_true=0.90,
            opportunity=50,
            undetermined_prob=0.60,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
        ),
        _simulate_iid_stratum(
            rng,
            name="null_small_sample_o016",
            regime="null",
            scenarios=scenarios_per_stratum,
            p_true=0.90,
            opportunity=16,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
        ),
    )

    clustered = _simulate_clustered_null_stratum(
        rng,
        scenarios=scenarios_per_stratum,
        tau_confidence=tau_confidence,
        tau_coverage=tau_coverage,
        bootstrap_resamples=bootstrap_resamples,
    )
    planted = _simulate_iid_stratum(
        rng,
        name="planted_signal_p099_o080",
        regime="planted_signal",
        scenarios=scenarios_per_stratum,
        p_true=0.99,
        opportunity=80,
        tau_confidence=tau_confidence,
        tau_coverage=tau_coverage,
    )
    r3_guard, r2_far = _simulate_r2_regression_control(scenarios=scenarios_per_stratum, tau_confidence=tau_confidence)

    null_strata = (*iid_nulls, clustered, r3_guard)
    total_null = sum(s.scenarios for s in null_strata)
    total_false_admitted = sum(s.admitted for s in null_strata)

    # Aggregate FAR_tau via compute_far_tau so the suite exercises the SD-002 FAR_tau instrument directly.
    pairs: list[tuple[bool, bool]] = []
    scenario_flags: list[bool] = []
    for stratum in null_strata:
        pairs.extend([(True, False)] * stratum.admitted)
        pairs.extend([(False, False)] * (stratum.scenarios - stratum.admitted))
        scenario_flags.extend([True] * stratum.admitted)
        scenario_flags.extend([False] * (stratum.scenarios - stratum.admitted))
    pairs.extend([(True, True)] * planted.admitted)
    pairs.extend([(False, True)] * (planted.scenarios - planted.admitted))
    far_report = compute_far_tau(pairs, tau_confidence=tau_confidence, scenario_outcomes=scenario_flags)

    null_far = total_false_admitted / total_null
    null_far_upper = far_report.scenario_cp_upper if far_report.scenario_cp_upper is not None else 1.0
    naive_clustered = clustered.naive_clustered_admission_rate or 0.0

    # per stratum, not only pooled: a single stratum at 16% would still pool with strata that never admit and pass
    worst_stratum = max(null_strata, key=lambda stratum: stratum.admission_rate)
    worst_stratum_upper = worst_stratum.cp_upper
    passed = (
        null_far <= MAX_NULL_FAR
        and null_far_upper <= MAX_NULL_FAR
        and worst_stratum.admission_rate <= MAX_NULL_FAR
        and clustered.admission_rate <= MAX_NULL_FAR
        and naive_clustered > MAX_NULL_FAR
        and planted.admission_rate >= 0.80
        and r2_far == 1.0
        and r3_guard.admission_rate == 0.0
    )

    return AdmissionCalibrationReport(
        seed=seed,
        tau_confidence=tau_confidence,
        tau_coverage=tau_coverage,
        null_scenarios=total_null,
        null_false_admissions=total_false_admitted,
        null_far_tau=null_far,
        null_far_cp_upper=null_far_upper,
        clustered_naive_far=naive_clustered,
        clustered_adjusted_far=clustered.admission_rate,
        planted_signal_admission_rate=planted.admission_rate,
        r2_baseline_false_admission_rate=r2_far,
        strata=(*null_strata, planted),
        worst_stratum_admission_rate=worst_stratum.admission_rate,
        worst_stratum_cp_upper=worst_stratum_upper,
        calibration_passed=passed,
    )


__all__ = [
    "MAX_NULL_FAR",
    "AdmissionCalibrationReport",
    "StratumResult",
    "run_admission_calibration",
]
