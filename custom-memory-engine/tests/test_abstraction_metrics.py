"""Unit tests for SD-001 abstraction metrics and pure-stdlib reference cross-checks."""

from __future__ import annotations

import pytest

from evaluation.stats.abstraction_metrics import (
    accuracy,
    brier_score,
    clopper_pearson_bounds,
    confidence_lower,
    counter_rate,
    coverage,
    cp_lower,
    expected_calibration_error,
    failure_rate_interval,
    partial_identification_cp_bounds,
    precision_recall,
    reference_accuracy,
    reference_brier_score,
    reference_counter_rate,
    reference_coverage,
    reference_cp_lower,
    reference_cp_upper,
    reference_ece,
    reference_precision_recall,
    reference_undetermined_rate,
    resolved_failure_rate,
    summarize_abstraction_metrics,
    undetermined_rate,
)
from validation.cp_bounds import cp_upper


def test_ledger_rates_and_partial_identification_bounds() -> None:
    assert coverage(30, 2, 8) == pytest.approx(32 / 40)
    assert accuracy(30, 2, 8) == pytest.approx(30 / 40)
    assert counter_rate(30, 2, 8) == pytest.approx(2 / 40)
    assert undetermined_rate(30, 2, 8) == pytest.approx(8 / 40)
    assert resolved_failure_rate(30, 2) == pytest.approx(2 / 32)
    assert resolved_failure_rate(0, 0) is None
    assert failure_rate_interval(30, 2, 8) == pytest.approx((2 / 40, 10 / 40))

    assert coverage(30, 2, 8) == pytest.approx(reference_coverage(30, 2, 8))
    assert accuracy(30, 2, 8) == pytest.approx(reference_accuracy(30, 2, 8))
    assert counter_rate(30, 2, 8) == pytest.approx(reference_counter_rate(30, 2, 8))
    assert undetermined_rate(30, 2, 8) == pytest.approx(reference_undetermined_rate(30, 2, 8))


def test_clopper_pearson_and_r3_conservative_lower_confidence() -> None:
    for failures, trials in ((0, 8), (0, 29), (0, 36), (0, 40), (2, 40), (45, 50), (40, 40)):
        lo, hi = clopper_pearson_bounds(failures, trials)
        assert lo == pytest.approx(cp_lower(failures, trials), abs=1e-9)
        assert lo == pytest.approx(reference_cp_lower(failures, trials), abs=1e-9)
        assert hi == pytest.approx(cp_upper(failures, trials), abs=1e-9)
        assert hi == pytest.approx(reference_cp_upper(failures, trials), abs=1e-9)

    assert confidence_lower(36, 0, 0) >= 0.90
    assert confidence_lower(29, 0, 0) < 0.90
    # R3 regression: sparse observations (5 support, 45 undetermined) treat u as potential failures.
    sparse_lo, sparse_hi = partial_identification_cp_bounds(5, 0, 45)
    assert sparse_lo == pytest.approx(0.0)
    assert sparse_hi == pytest.approx(cp_upper(45, 50), abs=1e-9)
    assert confidence_lower(5, 0, 45) == pytest.approx(1.0 - sparse_hi, abs=1e-9)
    assert confidence_lower(5, 0, 45) < 0.10


def test_summarize_precision_recall_and_calibration() -> None:
    untested = summarize_abstraction_metrics(36, 0, 0, refutation_attempts=0)
    assert untested.opportunity == 36
    assert untested.refutation_status == "untested"
    assert untested.confidence_lower >= 0.90

    tested = summarize_abstraction_metrics(30, 2, 8, refutation_attempts=3)
    assert tested.refutation_status == "tested"
    assert tested.point_failure_interval == pytest.approx((2 / 40, 10 / 40))

    pr = precision_recall(18, 2, 5, true_negatives=25)
    ref_prec, ref_rec = reference_precision_recall(18, 2, 5)
    assert pr.precision is not None and pr.recall is not None
    assert pr.precision == pytest.approx(ref_prec)
    assert pr.recall == pytest.approx(ref_rec)
    assert pr.precision_cp_lower is not None and pr.precision_cp_upper is not None
    assert pr.precision_cp_lower < pr.precision < pr.precision_cp_upper
    assert pr.base_rate == pytest.approx(23 / 50)

    empty_pr = precision_recall(0, 0, 0)
    assert (empty_pr.precision, empty_pr.recall, empty_pr.base_rate) == (None, None, None)

    probs = (0.9, 0.8, 0.2, 0.1)
    outcomes = (1, 1, 0, 0)
    assert brier_score(probs, outcomes) == pytest.approx(reference_brier_score(probs, outcomes), abs=1e-9)
    ece = expected_calibration_error(probs, outcomes, n_bins=5)
    assert ece.monitoring_only is True
    assert ece.ece == pytest.approx(reference_ece(probs, outcomes, n_bins=5), abs=1e-9)


def test_invalid_metric_inputs_fail_loudly() -> None:
    with pytest.raises(ValueError):
        coverage(0, 0, 0)
    with pytest.raises(ValueError):
        summarize_abstraction_metrics(-1, 0, 1)
    with pytest.raises(ValueError):
        brier_score([1.2], [1])
    with pytest.raises(ValueError):
        expected_calibration_error([0.5], [1, 0])
