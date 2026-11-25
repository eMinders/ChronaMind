"""Unit tests for the G4 synthetic null-calibration suite."""

from __future__ import annotations

import pytest

from evaluation.suites import NullSuiteReport, run_null_suite


def test_g4_null_suite_passes_and_is_deterministic() -> None:
    first = run_null_suite(seed=20261125, scenarios_per_stratum=20, bootstrap_resamples=10_000)
    second = run_null_suite(seed=20261125, scenarios_per_stratum=20, bootstrap_resamples=10_000)
    assert isinstance(first, NullSuiteReport)
    assert first == second
    assert first.g4_passed is True
    assert first.null_far_tau <= 0.05
    assert first.clustered_adjusted_far <= 0.05
    assert first.clustered_adjusted_far <= first.clustered_naive_far
    assert first.planted_signal_admission_rate > 0.50
    assert first.r2_baseline_false_admission_rate > 0.50
    regimes = {s.regime for s in first.strata}
    assert regimes == {"null", "clustered_null", "planted_signal", "r2_regression_control"}


def test_null_suite_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="nonnegative_integer_seed_required"):
        run_null_suite(seed=-1)
    with pytest.raises(ValueError, match="nonnegative_integer_seed_required"):
        run_null_suite(seed=True)
    with pytest.raises(ValueError, match="at_least_20_scenarios_per_stratum_required"):
        run_null_suite(scenarios_per_stratum=19)
    with pytest.raises(ValueError, match="threshold_out_of_range"):
        run_null_suite(tau_confidence=1.0)
