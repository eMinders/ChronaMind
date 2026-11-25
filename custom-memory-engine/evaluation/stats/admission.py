"""Unified abstraction admission decision helper, Holm closed-testing, and FAR_tau (SD-002).

Consistent with ``PLAN/V2/STATISTICS.md`` §4.2, ``V2-D30`` (defaults ``tau_confidence = 0.90``,
``tau_coverage = 0.60``), and ``custom_memory_engine.soundness.admission``.

Not labelled "exact" in general: Clopper–Pearson bounds are exact only under exchangeability at fixed ``n``
(``STATISTICS.md`` §5.4.2). Under temporal or session clustering, callers supply ``effective_opportunity`` from
``evaluation.stats.dependence`` so the finite-sample bound cannot understate uncertainty.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import ceil, isfinite

from pydantic import Field, model_validator
from scipy.stats import binom

from evaluation.stats.abstraction_metrics import (
    TAIL_PROBABILITY,
    cp_lower,
    partial_identification_cp_bounds,
    resolved_failure_rate,
)
from evaluation.stats.clopper_pearson import cp_interval, cp_upper
from evaluation.stats.models import StatsRecord
from evaluation.stats.multiplicity import holm_adjust

DEFAULT_TAU_CONFIDENCE = 0.90  # V2-D30
DEFAULT_TAU_COVERAGE = 0.60  # V2-D30
MIN_REFUTATION_ATTEMPTS = 3


def _count(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(code)
    return value


def _threshold(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(code)
    parsed = float(value)
    if not isfinite(parsed) or not 0.0 < parsed < 1.0:
        raise ValueError(code)
    return parsed


class LedgerSummary(StatsRecord):
    """Partially identified failure-rate interval and confidence bound for ``(s, k, u)``."""

    support: int = Field(ge=0)
    counter: int = Field(ge=0)
    undetermined: int = Field(ge=0)
    opportunity: int = Field(ge=1)
    coverage: float = Field(ge=0.0, le=1.0)
    confidence_lower: float = Field(ge=0.0, le=1.0)
    failure_upper: float = Field(ge=0.0, le=1.0)
    rate_lower: float = Field(ge=0.0, le=1.0)
    rate_upper: float = Field(ge=0.0, le=1.0)
    cp_lower: float = Field(ge=0.0, le=1.0)
    cp_upper: float = Field(ge=0.0, le=1.0)
    resolved_only_failure_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def balanced_counts(self) -> LedgerSummary:
        if self.support + self.counter + self.undetermined != self.opportunity:
            raise ValueError("opportunity_count_mismatch")
        return self


class AdmissionResult(StatsRecord):
    """Per-abstraction admission verdict under ``STATISTICS.md`` §4.2."""

    admissible: bool
    reasons: tuple[str, ...]
    ledger: LedgerSummary
    effective_opportunity: int | None = Field(default=None, ge=1)
    effective_confidence_lower: float | None = Field(default=None, ge=0.0, le=1.0)
    refutation_attempts: int = Field(ge=0)
    negative_control_clean: bool
    tau_confidence: float = Field(gt=0.0, lt=1.0)
    tau_coverage: float = Field(gt=0.0, lt=1.0)


class ClosedTestingCandidate(StatsRecord):
    """One candidate abstraction in a multi-candidate closed-testing family."""

    candidate_id: str = Field(min_length=1)
    support: int = Field(ge=0)
    counter: int = Field(ge=0)
    undetermined: int = Field(ge=0)
    refutation_attempts: int = Field(ge=0)
    negative_control_clean: bool
    effective_opportunity: int | None = Field(default=None, ge=1)


class ClosedTestingOutcome(StatsRecord):
    """Holm-adjusted admission verdict for one candidate in a family."""

    candidate_id: str = Field(min_length=1)
    admissible: bool
    raw_p_value: float = Field(ge=0.0, le=1.0)
    holm_adjusted_p: float = Field(ge=0.0, le=1.0)
    reasons: tuple[str, ...]
    assessment: AdmissionResult


class FarTauReport(StatsRecord):
    """Empirical false-admission rate (``FAR_tau``) and finite-sample binomial bounds (``STATISTICS.md`` §7)."""

    tau_confidence: float = Field(gt=0.0, lt=1.0)
    total_candidates: int = Field(ge=0)
    admitted_count: int = Field(ge=0)
    false_admitted_count: int = Field(ge=0)
    false_admission_rate: float = Field(ge=0.0, le=1.0)
    cp_lower: float | None = Field(default=None, ge=0.0, le=1.0)
    cp_upper: float | None = Field(default=None, ge=0.0, le=1.0)
    scenario_total: int | None = Field(default=None, ge=1)
    scenario_false_admitted: int | None = Field(default=None, ge=0)
    scenario_far: float | None = Field(default=None, ge=0.0, le=1.0)
    scenario_cp_upper: float | None = Field(default=None, ge=0.0, le=1.0)


def ledger(
    support: int,
    counter: int,
    undetermined: int,
    *,
    tail_probability: float = TAIL_PROBABILITY,
) -> LedgerSummary:
    """Compute the partially identified ledger summary (``STATISTICS.md`` §2.2, §4.2, §10)."""
    s = _count(support, "support_count_invalid")
    k = _count(counter, "counter_count_invalid")
    u = _count(undetermined, "undetermined_count_invalid")
    o = s + k + u
    if o <= 0:
        raise ValueError("opportunity_count_must_be_positive")
    lo_cp, hi_cp = partial_identification_cp_bounds(s, k, u, tail_probability=tail_probability)
    return LedgerSummary(
        support=s,
        counter=k,
        undetermined=u,
        opportunity=o,
        coverage=(s + k) / o,
        confidence_lower=1.0 - hi_cp,
        failure_upper=hi_cp,
        rate_lower=k / o,
        rate_upper=(k + u) / o,
        cp_lower=lo_cp,
        cp_upper=hi_cp,
        resolved_only_failure_rate=resolved_failure_rate(s, k),
    )


def _scaled_trials(
    failures: int,
    opportunity: int,
    effective_opportunity: int | None,
) -> tuple[int, int]:
    """Scale ``(k + u, o)`` conservatively to ``n_eff <= o`` (never inflates ``o`` above raw ``o``)."""
    if effective_opportunity is None:
        return failures, opportunity
    n_eff = _count(effective_opportunity, "effective_opportunity_invalid")
    if n_eff == 0 or n_eff > opportunity:
        raise ValueError("effective_opportunity_must_be_between_1_and_raw_opportunity")
    if n_eff == opportunity:
        return failures, opportunity
    scaled_failures = min(n_eff, ceil(failures * (n_eff / opportunity)))
    return scaled_failures, n_eff


def assess_admission(
    support: int,
    counter: int,
    undetermined: int,
    *,
    refutation_attempts: int = MIN_REFUTATION_ATTEMPTS,
    negative_control_clean: bool = True,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
    effective_opportunity: int | None = None,
    tail_probability: float = TAIL_PROBABILITY,
) -> AdmissionResult:
    """Apply the ``STATISTICS.md`` §4.2 admission predicate with optional conservative ``n_eff`` adjustment."""
    attempts = _count(refutation_attempts, "refutation_attempts_invalid")
    if not isinstance(negative_control_clean, bool):
        raise ValueError("negative_control_flag_must_be_boolean")
    tau_conf = _threshold(tau_confidence, "threshold_out_of_range")
    tau_cov = _threshold(tau_coverage, "threshold_out_of_range")
    summary = ledger(support, counter, undetermined, tail_probability=tail_probability)
    o = summary.opportunity
    eff_failures, eff_trials = _scaled_trials(summary.counter + summary.undetermined, o, effective_opportunity)
    if effective_opportunity is not None:
        eff_upper = cp_upper(eff_failures, eff_trials, tail_probability=tail_probability)
        eff_conf = 1.0 - eff_upper
    else:
        eff_conf = summary.confidence_lower

    reasons: list[str] = []
    if eff_conf < tau_conf:
        reasons.append("confidence_lower_below_threshold")
    if summary.coverage < tau_cov:
        reasons.append("coverage_below_threshold")
    if attempts < MIN_REFUTATION_ATTEMPTS:
        reasons.append("too_few_refutation_attempts")
    if summary.counter == 0 and cp_upper(0, eff_trials, tail_probability=tail_probability) > 1.0 - tau_conf:
        reasons.append("zero_counter_opportunity_too_small")
    if not negative_control_clean:
        reasons.append("negative_control_slice_not_clean")

    return AdmissionResult(
        admissible=not reasons,
        reasons=tuple(reasons),
        ledger=summary,
        effective_opportunity=None if effective_opportunity is None else eff_trials,
        effective_confidence_lower=None if effective_opportunity is None else eff_conf,
        refutation_attempts=attempts,
        negative_control_clean=negative_control_clean,
        tau_confidence=tau_conf,
        tau_coverage=tau_cov,
    )


def candidate_failure_p_value(
    failures: int,
    trials: int,
    *,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
) -> float:
    """Exact one-sided binomial p-value for ``H0: p_f >= 1 - tau_confidence`` vs ``p_f < 1 - tau_confidence``."""
    k = _count(failures, "failures_must_be_a_nonnegative_integer")
    n = _count(trials, "trials_must_be_a_positive_integer")
    if n <= 0 or k > n:
        raise ValueError("failures_must_not_exceed_positive_trials")
    tau_conf = _threshold(tau_confidence, "threshold_out_of_range")
    null_failure_rate = 1.0 - tau_conf
    return float(min(1.0, max(0.0, binom.cdf(k, n, null_failure_rate))))


def closed_testing_admit(
    candidates: Sequence[ClosedTestingCandidate],
    *,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
    fwer_alpha: float = TAIL_PROBABILITY,
) -> tuple[ClosedTestingOutcome, ...]:
    """Compose per-abstraction admission with Holm step-down FWER control across candidate abstractions."""
    if not candidates:
        raise ValueError("nonempty_candidates_required")
    ids = [c.candidate_id for c in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_candidate_id")
    tau_conf = _threshold(tau_confidence, "threshold_out_of_range")
    tau_cov = _threshold(tau_coverage, "threshold_out_of_range")
    alpha = _threshold(fwer_alpha, "fwer_alpha_out_of_range")

    assessments: dict[str, AdmissionResult] = {}
    raw_p: dict[str, float] = {}
    for cand in candidates:
        assessment = assess_admission(
            cand.support,
            cand.counter,
            cand.undetermined,
            refutation_attempts=cand.refutation_attempts,
            negative_control_clean=cand.negative_control_clean,
            tau_confidence=tau_conf,
            tau_coverage=tau_cov,
            effective_opportunity=cand.effective_opportunity,
            tail_probability=alpha,
        )
        assessments[cand.candidate_id] = assessment
        eff_failures, eff_trials = _scaled_trials(
            cand.counter + cand.undetermined,
            assessment.ledger.opportunity,
            cand.effective_opportunity,
        )
        raw_p[cand.candidate_id] = candidate_failure_p_value(eff_failures, eff_trials, tau_confidence=tau_conf)

    adjusted_p = holm_adjust(raw_p, alpha=alpha)
    outcomes: list[ClosedTestingOutcome] = []
    for cand in candidates:
        assessment = assessments[cand.candidate_id]
        adj_p = adjusted_p[cand.candidate_id]
        reasons = list(assessment.reasons)
        if adj_p > alpha:
            reasons.append("holm_adjusted_p_above_alpha")
        outcomes.append(
            ClosedTestingOutcome(
                candidate_id=cand.candidate_id,
                admissible=assessment.admissible and adj_p <= alpha,
                raw_p_value=raw_p[cand.candidate_id],
                holm_adjusted_p=adj_p,
                reasons=tuple(reasons),
                assessment=assessment,
            )
        )
    return tuple(outcomes)


def compute_far_tau(
    outcomes: Sequence[tuple[bool, bool]],
    *,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    scenario_outcomes: Sequence[bool] | None = None,
    tail_probability: float = TAIL_PROBABILITY,
) -> FarTauReport:
    """Compute ``FAR_tau`` over ``(admitted, ground_truth_true)`` pairs plus optional scenario-level rate.

    ``FAR_tau`` is the proportion of admitted abstractions (``admitted=True``) that are actually false
    (``ground_truth_true=False``). When ``scenario_outcomes`` is supplied (each boolean indicating whether that
    null scenario suffered >= 1 false admission), its scenario-level rate and CP upper bound are reported too.
    """
    tau_conf = _threshold(tau_confidence, "threshold_out_of_range")
    tail = _threshold(tail_probability, "tail_probability_out_of_range")
    admitted_count = 0
    false_admitted = 0
    for item in outcomes:
        if not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(x, bool) for x in item):
            raise ValueError("outcomes_must_be_boolean_pairs")
        admitted, truth = item
        if admitted:
            admitted_count += 1
            if not truth:
                false_admitted += 1

    far = 0.0 if admitted_count == 0 else false_admitted / admitted_count
    confidence = 0.95 if tail == TAIL_PROBABILITY else 1.0 - 2.0 * tail
    if admitted_count > 0:
        lo, hi = cp_interval(false_admitted, admitted_count, confidence=confidence)
        lo_opt: float | None = lo
        hi_opt: float | None = hi
    else:
        lo_opt, hi_opt = cp_lower(0, 1, tail_probability=tail), None

    scen_total: int | None = None
    scen_false: int | None = None
    scen_far: float | None = None
    scen_upper: float | None = None
    if scenario_outcomes is not None:
        if not scenario_outcomes or not all(isinstance(x, bool) for x in scenario_outcomes):
            raise ValueError("scenario_outcomes_must_be_nonempty_booleans")
        scen_total = len(scenario_outcomes)
        scen_false = sum(1 for leaked in scenario_outcomes if leaked)
        scen_far = scen_false / scen_total
        scen_upper = cp_upper(scen_false, scen_total, tail_probability=tail)

    return FarTauReport(
        tau_confidence=tau_conf,
        total_candidates=len(outcomes),
        admitted_count=admitted_count,
        false_admitted_count=false_admitted,
        false_admission_rate=far,
        cp_lower=lo_opt,
        cp_upper=hi_opt,
        scenario_total=scen_total,
        scenario_false_admitted=scen_false,
        scenario_far=scen_far,
        scenario_cp_upper=scen_upper,
    )


__all__ = [
    "DEFAULT_TAU_CONFIDENCE",
    "DEFAULT_TAU_COVERAGE",
    "MIN_REFUTATION_ATTEMPTS",
    "TAIL_PROBABILITY",
    "AdmissionResult",
    "ClosedTestingCandidate",
    "ClosedTestingOutcome",
    "FarTauReport",
    "LedgerSummary",
    "assess_admission",
    "candidate_failure_p_value",
    "closed_testing_admit",
    "compute_far_tau",
    "ledger",
]
