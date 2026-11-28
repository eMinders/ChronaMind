"""The admission calibration study: size, controls, and per-stratum gating (`SD-002` self-check, not `G4`/`C-012`)."""

from __future__ import annotations

import pytest

from evaluation.suites import AdmissionCalibrationReport, run_admission_calibration

SEED = 20261125  # pinned so the reported counts are reproducible; the report records it for that reason


def test_the_calibration_is_deterministic_at_its_pinned_seed_and_every_property_holds() -> None:
    first = run_admission_calibration(seed=SEED, scenarios_per_stratum=20, bootstrap_resamples=10_000)
    second = run_admission_calibration(seed=SEED, scenarios_per_stratum=20, bootstrap_resamples=10_000)
    assert isinstance(first, AdmissionCalibrationReport)
    assert first == second
    assert first.calibration_passed is True
    assert first.null_far_tau <= 0.05
    # the pooled rate is not enough: every null stratum clears its own gate, and its bound is reported beside it
    assert first.worst_stratum_admission_rate <= 0.05
    assert first.worst_stratum_cp_upper > first.worst_stratum_admission_rate
    assert first.clustered_adjusted_far <= 0.05
    assert first.clustered_adjusted_far <= first.clustered_naive_far
    assert first.planted_signal_admission_rate > 0.50  # a rule that never admits must not pass
    assert first.r2_baseline_false_admission_rate > 0.50
    regimes = {s.regime for s in first.strata}
    assert regimes == {"null", "clustered_null", "planted_signal", "r2_regression_control"}


def test_the_worst_stratum_is_the_highest_admission_rate_and_pooling_cannot_hide_it() -> None:
    """A failing stratum cannot be hidden by pooling it with strata that never admit (finding B5)."""
    report = run_admission_calibration(seed=SEED, scenarios_per_stratum=20, bootstrap_resamples=10_000)
    null_strata = [s for s in report.strata if s.regime != "planted_signal"]
    worst = max(null_strata, key=lambda s: s.admission_rate)
    assert report.worst_stratum_admission_rate == worst.admission_rate
    assert report.worst_stratum_cp_upper == worst.cp_upper
    assert report.null_scenarios == sum(s.scenarios for s in null_strata)
    # the pooled rate can be strictly lower than the worst stratum: that is exactly what per-stratum gating prevents
    assert report.null_far_tau <= report.worst_stratum_admission_rate


def test_the_calibration_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="nonnegative_integer_seed_required"):
        run_admission_calibration(seed=-1)
    with pytest.raises(ValueError, match="nonnegative_integer_seed_required"):
        run_admission_calibration(seed=True)
    with pytest.raises(ValueError, match="at_least_20_scenarios_per_stratum_required"):
        run_admission_calibration(scenarios_per_stratum=19)
    with pytest.raises(ValueError, match="threshold_out_of_range"):
        run_admission_calibration(tau_confidence=1.0)
