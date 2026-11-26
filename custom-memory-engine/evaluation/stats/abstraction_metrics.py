"""Pure, deterministic abstraction metrics with hand-computable reference implementations (SD-001).

Implements the three-state partial-identification metrics of ``PLAN/V2/STATISTICS.md`` (§2.2, §4.2, §4.3, §7):

* ``o = s + k + u`` (support + counter + undetermined);
* ``coverage = (s + k) / o`` (gate on censored/missing outcomes);
* ``confidence_lower = 1 - CP_upper(k + u, o)`` at the single system convention (one-sided 97.5% = ``alpha = 0.05``);
* set-level monitoring metrics (precision, recall, Brier score, ECE + reliability bins, ``FAR_tau``).

ECE and Brier score are strictly **monitoring** metrics (``STATISTICS.md`` §4.3) and never admission gates.
Both ECE implementations bin with one rule: bin ``b`` is the half-open ``[b / n_bins, (b + 1) / n_bins)``, the
last bin closed at 1.0, with edges computed as ``b / n_bins``.

The admission helper built on these bounds (``evaluation.stats.admission``) implements two of ``STATISTICS.md``
§5.4.3's three conditions (per-abstraction bound and family-wise significance); time-uniform validity is SD-005
and is not part of this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import comb, isfinite
from typing import Literal

from pydantic import Field, model_validator

from evaluation.stats.clopper_pearson import cp_interval, cp_upper
from evaluation.stats.models import StatsRecord

TAIL_PROBABILITY = 0.025  # one-sided 97.5% == two-sided 95% (STATISTICS.md §4.2)


def _nonneg_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(code)
    return value


def _pos_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(code)
    return value


def _unit_float(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(code)
    parsed = float(value)
    if not isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ValueError(code)
    return parsed


def _tail(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("tail_probability_out_of_range")
    parsed = float(value)
    if not isfinite(parsed) or not 0.0 < parsed < 0.5:
        raise ValueError("tail_probability_out_of_range")
    return parsed


def _ledger(support: int, counter: int, undetermined: int) -> tuple[int, int, int, int]:
    s = _nonneg_int(support, "support_count_invalid")
    k = _nonneg_int(counter, "counter_count_invalid")
    u = _nonneg_int(undetermined, "undetermined_count_invalid")
    o = s + k + u
    if o <= 0:
        raise ValueError("opportunity_count_must_be_positive")
    return s, k, u, o


def _binary_sequence(values: Sequence[int | bool], code: str) -> tuple[int, ...]:
    out: list[int] = []
    for item in values:
        if isinstance(item, bool):
            out.append(1 if item else 0)
        elif isinstance(item, int) and item in (0, 1):
            out.append(item)
        else:
            raise ValueError(code)
    return tuple(out)


def _prob_sequence(values: Sequence[float], code: str) -> tuple[float, ...]:
    return tuple(_unit_float(item, code) for item in values)


class LedgerCounts(StatsRecord):
    """Validated three-state ledger ``(s, k, u)`` with positive opportunity ``o = s + k + u``."""

    support: int = Field(ge=0)
    counter: int = Field(ge=0)
    undetermined: int = Field(ge=0)

    @model_validator(mode="after")
    def require_positive_opportunity(self) -> LedgerCounts:
        if self.opportunity <= 0:
            raise ValueError("opportunity_count_must_be_positive")
        return self

    @property
    def opportunity(self) -> int:
        return self.support + self.counter + self.undetermined


class PrecisionRecallMetrics(StatsRecord):
    """Set-level precision, recall, CP interval on precision, and base-rate baseline (``STATISTICS.md`` §7)."""

    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    total_evaluated: int = Field(ge=0)
    precision: float | None = Field(default=None, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, ge=0.0, le=1.0)
    precision_cp_lower: float | None = Field(default=None, ge=0.0, le=1.0)
    precision_cp_upper: float | None = Field(default=None, ge=0.0, le=1.0)
    base_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class ReliabilityBin(StatsRecord):
    """One bin of a reliability diagram (``STATISTICS.md`` §4.3, monitoring only)."""

    bin_index: int = Field(ge=0)
    lower_bound: float = Field(ge=0.0, le=1.0)
    upper_bound: float = Field(ge=0.0, le=1.0)
    count: int = Field(ge=0)
    mean_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    empirical_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    calibration_gap: float | None = Field(default=None, ge=0.0, le=1.0)


class CalibrationSummary(StatsRecord):
    """ECE, Brier score, and reliability bins (``STATISTICS.md`` §4.3 — monitoring only, never an admission gate)."""

    count: int = Field(ge=1)
    n_bins: int = Field(ge=1)
    ece: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    bins: tuple[ReliabilityBin, ...] = Field(min_length=1)
    monitoring_only: Literal[True] = True


class AbstractionMetricsReport(StatsRecord):
    """Complete deterministic metrics summary for one abstraction ledger."""

    support: int = Field(ge=0)
    counter: int = Field(ge=0)
    undetermined: int = Field(ge=0)
    opportunity: int = Field(ge=1)
    coverage: float = Field(ge=0.0, le=1.0)
    accuracy: float = Field(ge=0.0, le=1.0)
    counter_rate: float = Field(ge=0.0, le=1.0)
    undetermined_rate: float = Field(ge=0.0, le=1.0)
    resolved_failure_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    point_failure_interval: tuple[float, float]
    cp_failure_interval: tuple[float, float]
    confidence_lower: float = Field(ge=0.0, le=1.0)
    refutation_attempts: int = Field(ge=0)
    refutation_status: Literal["untested", "tested"]


def coverage(support: int, counter: int, undetermined: int) -> float:
    """Resolved opportunity share ``(s + k) / o`` (``STATISTICS.md`` §2.2)."""
    s, k, _u, o = _ledger(support, counter, undetermined)
    return (s + k) / o


def accuracy(support: int, counter: int, undetermined: int) -> float:
    """Observed support proportion ``s / o`` over all opportunities."""
    s, _k, _u, o = _ledger(support, counter, undetermined)
    return s / o


def counter_rate(support: int, counter: int, undetermined: int) -> float:
    """Observed counterexample rate ``k / o`` (point lower bound on ``p_f``)."""
    _s, k, _u, o = _ledger(support, counter, undetermined)
    return k / o


def undetermined_rate(support: int, counter: int, undetermined: int) -> float:
    """Unresolved / censored opportunity share ``u / o``."""
    _s, _k, u, o = _ledger(support, counter, undetermined)
    return u / o


def resolved_failure_rate(support: int, counter: int) -> float | None:
    """Diagnostic failure rate ``k / (s + k)`` conditional on resolution; never decides admission."""
    s = _nonneg_int(support, "support_count_invalid")
    k = _nonneg_int(counter, "counter_count_invalid")
    resolved = s + k
    return None if resolved == 0 else k / resolved


def failure_rate_interval(support: int, counter: int, undetermined: int) -> tuple[float, float]:
    """Point-identified partial-identification interval ``[k / o, (k + u) / o]``."""
    _s, k, u, o = _ledger(support, counter, undetermined)
    return (k / o, (k + u) / o)


def cp_lower(failures: int, trials: int, *, tail_probability: float = TAIL_PROBABILITY) -> float:
    """One-sided Clopper–Pearson lower limit with coverage ``1 - tail_probability``.

    Finite-sample at fixed ``trials``, valid under exchangeability only (``STATISTICS.md`` §5.4.2).
    """
    k = _nonneg_int(failures, "failures_must_be_a_nonnegative_integer")
    n = _pos_int(trials, "trials_must_be_a_positive_integer")
    if k > n:
        raise ValueError("failures_must_not_exceed_trials")
    tail = _tail(tail_probability)
    if k == 0:
        return 0.0
    confidence = 0.95 if tail == TAIL_PROBABILITY else 1.0 - 2.0 * tail
    lower, _ = cp_interval(k, n, confidence=confidence)
    return lower


def clopper_pearson_bounds(
    failures: int,
    trials: int,
    *,
    tail_probability: float = TAIL_PROBABILITY,
) -> tuple[float, float]:
    """Two-sided Clopper–Pearson bounds ``(CP_lower(k, n), CP_upper(k, n))``."""
    k = _nonneg_int(failures, "failures_must_be_a_nonnegative_integer")
    n = _pos_int(trials, "trials_must_be_a_positive_integer")
    if k > n:
        raise ValueError("failures_must_not_exceed_trials")
    tail = _tail(tail_probability)
    return (
        cp_lower(k, n, tail_probability=tail),
        cp_upper(k, n, tail_probability=tail),
    )


def partial_identification_cp_bounds(
    support: int,
    counter: int,
    undetermined: int,
    *,
    tail_probability: float = TAIL_PROBABILITY,
) -> tuple[float, float]:
    """Finite-sample partial-identification bounds ``[CP_lower(k, o), CP_upper(k + u, o)]``."""
    _s, k, u, o = _ledger(support, counter, undetermined)
    tail = _tail(tail_probability)
    return (
        cp_lower(k, o, tail_probability=tail),
        cp_upper(k + u, o, tail_probability=tail),
    )


def confidence_lower(
    support: int,
    counter: int,
    undetermined: int,
    *,
    tail_probability: float = TAIL_PROBABILITY,
) -> float:
    """Conservative confidence lower bound ``1 - CP_upper(k + u, o)`` (valid under exchangeability)."""
    _s, k, u, o = _ledger(support, counter, undetermined)
    tail = _tail(tail_probability)
    return 1.0 - cp_upper(k + u, o, tail_probability=tail)


def summarize_abstraction_metrics(
    support: int,
    counter: int,
    undetermined: int,
    *,
    refutation_attempts: int = 0,
    tail_probability: float = TAIL_PROBABILITY,
) -> AbstractionMetricsReport:
    """Compute the full per-abstraction metrics report from ``(s, k, u)`` and ``refutation_attempts``."""
    s, k, u, o = _ledger(support, counter, undetermined)
    attempts = _nonneg_int(refutation_attempts, "refutation_attempts_invalid")
    tail = _tail(tail_probability)
    cp_bounds = partial_identification_cp_bounds(s, k, u, tail_probability=tail)
    return AbstractionMetricsReport(
        support=s,
        counter=k,
        undetermined=u,
        opportunity=o,
        coverage=(s + k) / o,
        accuracy=s / o,
        counter_rate=k / o,
        undetermined_rate=u / o,
        resolved_failure_rate=resolved_failure_rate(s, k),
        point_failure_interval=failure_rate_interval(s, k, u),
        cp_failure_interval=cp_bounds,
        confidence_lower=1.0 - cp_bounds[1],
        refutation_attempts=attempts,
        refutation_status="untested" if attempts == 0 else "tested",
    )


def precision_recall(
    true_positives: int,
    false_positives: int,
    false_negatives: int,
    *,
    true_negatives: int = 0,
    tail_probability: float = TAIL_PROBABILITY,
) -> PrecisionRecallMetrics:
    """Precision (with CP interval), recall, and positive base rate (``STATISTICS.md`` §7)."""
    tp = _nonneg_int(true_positives, "true_positives_invalid")
    fp = _nonneg_int(false_positives, "false_positives_invalid")
    fn = _nonneg_int(false_negatives, "false_negatives_invalid")
    tn = _nonneg_int(true_negatives, "true_negatives_invalid")
    tail = _tail(tail_probability)
    predicted_pos = tp + fp
    actual_pos = tp + fn
    total = tp + fp + fn + tn
    prec = None if predicted_pos == 0 else tp / predicted_pos
    rec = None if actual_pos == 0 else tp / actual_pos
    prec_lo, prec_hi = (
        (None, None) if predicted_pos == 0 else clopper_pearson_bounds(tp, predicted_pos, tail_probability=tail)
    )
    base = None if total == 0 else actual_pos / total
    return PrecisionRecallMetrics(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        total_evaluated=total,
        precision=prec,
        recall=rec,
        precision_cp_lower=prec_lo,
        precision_cp_upper=prec_hi,
        base_rate=base,
    )


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int | bool]) -> float:
    """Mean squared error between predicted probabilities and binary outcomes."""
    probs = _prob_sequence(probabilities, "invalid_probability")
    ys = _binary_sequence(outcomes, "invalid_binary_outcome")
    if not probs or len(probs) != len(ys):
        raise ValueError("nonempty_equal_length_sequences_required")
    return sum((p - y) ** 2 for p, y in zip(probs, ys, strict=True)) / len(probs)


def _calibration_bin(probability: float, n_bins: int) -> int:
    """Index of the half-open bin ``[b / n_bins, (b + 1) / n_bins)`` holding ``probability``; the last bin is closed.

    ``int(p * n_bins)`` alone can disagree with the edge ``b / n_bins`` by one ulp (``0.57 * 100 < 57``), so the
    candidate index is corrected against the same edges ``reference_ece`` compares with.
    """
    idx = min(n_bins - 1, int(probability * n_bins))
    while idx > 0 and probability < idx / n_bins:
        idx -= 1
    while idx < n_bins - 1 and probability >= (idx + 1) / n_bins:
        idx += 1
    return idx


def expected_calibration_error(
    probabilities: Sequence[float],
    outcomes: Sequence[int | bool],
    *,
    n_bins: int = 10,
) -> CalibrationSummary:
    """Compute ECE, Brier score, and reliability bins (``STATISTICS.md`` §4.3 — monitoring only)."""
    probs = _prob_sequence(probabilities, "invalid_probability")
    ys = _binary_sequence(outcomes, "invalid_binary_outcome")
    bins_count = _pos_int(n_bins, "n_bins_must_be_positive")
    if not probs or len(probs) != len(ys):
        raise ValueError("nonempty_equal_length_sequences_required")
    total = len(probs)
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins_count)]
    for p, y in zip(probs, ys, strict=True):
        buckets[_calibration_bin(p, bins_count)].append((p, y))
    bins: list[ReliabilityBin] = []
    ece = 0.0
    for idx, items in enumerate(buckets):
        lo = idx / bins_count
        hi = (idx + 1) / bins_count
        if not items:
            bins.append(ReliabilityBin(bin_index=idx, lower_bound=lo, upper_bound=hi, count=0))
            continue
        mean_conf = sum(p for p, _ in items) / len(items)
        emp_acc = sum(y for _, y in items) / len(items)
        gap = abs(emp_acc - mean_conf)
        ece += (len(items) / total) * gap
        bins.append(
            ReliabilityBin(
                bin_index=idx,
                lower_bound=lo,
                upper_bound=hi,
                count=len(items),
                mean_confidence=mean_conf,
                empirical_accuracy=emp_acc,
                calibration_gap=gap,
            )
        )
    return CalibrationSummary(
        count=total,
        n_bins=bins_count,
        ece=min(1.0, ece),
        brier_score=brier_score(probs, ys),
        bins=tuple(bins),
    )


# --- Hand-computable pure-stdlib reference API (SD-001) -----------------------------------------------


def _binom_cdf_exact(k: int, n: int, p: float) -> float:
    return sum(comb(n, i) * (p**i) * ((1.0 - p) ** (n - i)) for i in range(k + 1))


def reference_cp_upper(failures: int, trials: int, *, tail_probability: float = TAIL_PROBABILITY) -> float:
    """Reference Clopper–Pearson upper limit using exact binomial sums and bisection (pure stdlib)."""
    k = _nonneg_int(failures, "failures_must_be_a_nonnegative_integer")
    n = _pos_int(trials, "trials_must_be_a_positive_integer")
    if k > n:
        raise ValueError("failures_must_not_exceed_trials")
    tail = _tail(tail_probability)
    if k == n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _binom_cdf_exact(k, n, mid) > tail:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def reference_cp_lower(failures: int, trials: int, *, tail_probability: float = TAIL_PROBABILITY) -> float:
    """Reference Clopper–Pearson lower limit using exact binomial tail inversion and bisection (pure stdlib)."""
    k = _nonneg_int(failures, "failures_must_be_a_nonnegative_integer")
    n = _pos_int(trials, "trials_must_be_a_positive_integer")
    if k > n:
        raise ValueError("failures_must_not_exceed_trials")
    tail = _tail(tail_probability)
    if k == 0:
        return 0.0
    return 1.0 - reference_cp_upper(n - k, n, tail_probability=tail)


def reference_coverage(support: int, counter: int, undetermined: int) -> float:
    """Reference hand-computable coverage ``(s + k) / (s + k + u)``."""
    s, k, _u, o = _ledger(support, counter, undetermined)
    return (s + k) / o


def reference_accuracy(support: int, counter: int, undetermined: int) -> float:
    """Reference hand-computable accuracy ``s / (s + k + u)``."""
    s, _k, _u, o = _ledger(support, counter, undetermined)
    return s / o


def reference_counter_rate(support: int, counter: int, undetermined: int) -> float:
    """Reference hand-computable counter rate ``k / (s + k + u)``."""
    _s, k, _u, o = _ledger(support, counter, undetermined)
    return k / o


def reference_undetermined_rate(support: int, counter: int, undetermined: int) -> float:
    """Reference hand-computable undetermined rate ``u / (s + k + u)``."""
    _s, _k, u, o = _ledger(support, counter, undetermined)
    return u / o


def reference_brier_score(probabilities: Sequence[float], outcomes: Sequence[int | bool]) -> float:
    """Reference hand-computable Brier score."""
    probs = _prob_sequence(probabilities, "invalid_probability")
    ys = _binary_sequence(outcomes, "invalid_binary_outcome")
    if not probs or len(probs) != len(ys):
        raise ValueError("nonempty_equal_length_sequences_required")
    total_sq = 0.0
    for i in range(len(probs)):
        diff = probs[i] - ys[i]
        total_sq += diff * diff
    return total_sq / len(probs)


def reference_ece(probabilities: Sequence[float], outcomes: Sequence[int | bool], *, n_bins: int = 10) -> float:
    """Reference hand-computable ECE, binned by the half-open ``[lo, hi)`` rule with the last bin closed."""
    probs = _prob_sequence(probabilities, "invalid_probability")
    ys = _binary_sequence(outcomes, "invalid_binary_outcome")
    bins_count = _pos_int(n_bins, "n_bins_must_be_positive")
    if not probs or len(probs) != len(ys):
        raise ValueError("nonempty_equal_length_sequences_required")
    ece = 0.0
    n = len(probs)
    for b in range(bins_count):
        lo = b / bins_count
        hi = (b + 1) / bins_count
        in_bin = [
            (p, y) for p, y in zip(probs, ys, strict=True) if (lo <= p <= hi if b == bins_count - 1 else lo <= p < hi)
        ]
        if in_bin:
            conf = sum(p for p, _ in in_bin) / len(in_bin)
            acc = sum(y for _, y in in_bin) / len(in_bin)
            ece += (len(in_bin) / n) * abs(acc - conf)
    return min(1.0, ece)


def reference_precision_recall(
    true_positives: int, false_positives: int, false_negatives: int
) -> tuple[float | None, float | None]:
    """Reference hand-computable ``(precision, recall)``."""
    tp = _nonneg_int(true_positives, "true_positives_invalid")
    fp = _nonneg_int(false_positives, "false_positives_invalid")
    fn = _nonneg_int(false_negatives, "false_negatives_invalid")
    prec = None if tp + fp == 0 else tp / (tp + fp)
    rec = None if tp + fn == 0 else tp / (tp + fn)
    return prec, rec


__all__ = [
    "TAIL_PROBABILITY",
    "AbstractionMetricsReport",
    "CalibrationSummary",
    "LedgerCounts",
    "PrecisionRecallMetrics",
    "ReliabilityBin",
    "accuracy",
    "brier_score",
    "clopper_pearson_bounds",
    "confidence_lower",
    "counter_rate",
    "coverage",
    "cp_lower",
    "expected_calibration_error",
    "failure_rate_interval",
    "partial_identification_cp_bounds",
    "precision_recall",
    "reference_accuracy",
    "reference_brier_score",
    "reference_counter_rate",
    "reference_coverage",
    "reference_cp_lower",
    "reference_cp_upper",
    "reference_ece",
    "reference_precision_recall",
    "reference_undetermined_rate",
    "resolved_failure_rate",
    "summarize_abstraction_metrics",
    "undetermined_rate",
]
