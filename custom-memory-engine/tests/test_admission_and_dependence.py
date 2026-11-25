"""Unit tests for SD-002 admission, FWER closed testing, FAR_tau, and SD-008 dependence."""

from __future__ import annotations

import pytest

from evaluation.stats.admission import (
    DEFAULT_TAU_CONFIDENCE,
    DEFAULT_TAU_COVERAGE,
    ClosedTestingCandidate,
    assess_admission,
    candidate_failure_p_value,
    closed_testing_admit,
    compute_far_tau,
    ledger,
)
from evaluation.stats.dependence import (
    ClusterObservation,
    assess_dependence,
    block_bootstrap_failure_upper,
    design_effect_sample_size,
    estimate_intraclass_correlation,
)
from validation.cp_bounds import cp_upper


def test_ledger_and_assess_admission_thresholds() -> None:
    assert (DEFAULT_TAU_CONFIDENCE, DEFAULT_TAU_COVERAGE) == (0.90, 0.60)
    r3 = ledger(5, 0, 45)
    assert r3.opportunity == 50
    assert r3.coverage == pytest.approx(0.10)
    assert r3.failure_upper == pytest.approx(cp_upper(45, 50), abs=1e-9)
    assert r3.confidence_lower == pytest.approx(1.0 - cp_upper(45, 50), abs=1e-9)

    assert assess_admission(36, 0, 0, refutation_attempts=3, negative_control_clean=True).admissible is True
    assert assess_admission(29, 0, 0, refutation_attempts=3, negative_control_clean=True).admissible is False

    no_ref = assess_admission(40, 0, 0, refutation_attempts=2, negative_control_clean=True)
    assert not no_ref.admissible and no_ref.reasons

    dirty = assess_admission(40, 0, 0, refutation_attempts=3, negative_control_clean=False)
    assert not dirty.admissible and dirty.reasons

    low_cov = assess_admission(36, 0, 30, refutation_attempts=3, negative_control_clean=True)
    assert not low_cov.admissible

    dep_rejected = assess_admission(
        40, 0, 0, refutation_attempts=3, negative_control_clean=True, effective_opportunity=25
    )
    assert not dep_rejected.admissible
    assert dep_rejected.effective_confidence_lower == pytest.approx(1.0 - cp_upper(0, 25), abs=1e-9)


def test_closed_testing_and_far_tau() -> None:
    p_strong = candidate_failure_p_value(0, 80)
    p_weak = candidate_failure_p_value(0, 20)
    assert p_strong < p_weak

    candidates = (
        ClosedTestingCandidate(
            candidate_id="c-strong",
            support=80,
            counter=0,
            undetermined=0,
            refutation_attempts=3,
            negative_control_clean=True,
        ),
        ClosedTestingCandidate(
            candidate_id="c-borderline-1",
            support=36,
            counter=0,
            undetermined=0,
            refutation_attempts=3,
            negative_control_clean=True,
        ),
        ClosedTestingCandidate(
            candidate_id="c-borderline-2",
            support=36,
            counter=0,
            undetermined=0,
            refutation_attempts=3,
            negative_control_clean=True,
        ),
        ClosedTestingCandidate(
            candidate_id="c-untested",
            support=80,
            counter=0,
            undetermined=0,
            refutation_attempts=1,
            negative_control_clean=True,
        ),
    )
    outcomes = closed_testing_admit(candidates)
    by_id = {o.candidate_id: o for o in outcomes}
    assert by_id["c-strong"].admissible is True
    assert by_id["c-borderline-1"].admissible is False  # multiplicity adjustment across 2 remaining hypotheses
    assert by_id["c-borderline-2"].admissible is False
    assert by_id["c-untested"].admissible is False

    far = compute_far_tau(
        [(True, True), (True, False), (False, False), (False, True)],
        scenario_outcomes=[True, False],
    )
    assert far.total_candidates == 4
    assert far.admitted_count == 2
    assert far.false_admitted_count == 1
    assert far.false_admission_rate == pytest.approx(0.5)
    assert far.scenario_total == 2
    assert far.scenario_false_admitted == 1
    assert far.scenario_far == pytest.approx(0.5)


def test_dependence_correction_is_conservative_and_deterministic() -> None:
    assert design_effect_sample_size(40, 4.0, 0.0) == 40
    assert design_effect_sample_size(40, 4.0, 0.20) == 25

    clusters = [[0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0], [0, 0, 0, 0]]
    rho_hat, rho_upper = estimate_intraclass_correlation(clusters)
    assert 0.0 <= rho_hat <= rho_upper <= 1.0

    b1 = block_bootstrap_failure_upper(clusters, seed=7, resamples=10_000)
    b2 = block_bootstrap_failure_upper(clusters, seed=7, resamples=10_000)
    assert b1 == b2
    assert 0.0 <= b1 <= 1.0
    with pytest.raises(ValueError, match="at_least_10000_bootstrap_resamples_required"):
        block_bootstrap_failure_upper(clusters, seed=7, resamples=500)

    obs: list[ClusterObservation] = []
    for cid in range(10):
        outcome = "counter" if cid == 0 else "support"
        for _ in range(4):
            obs.append(ClusterObservation(cluster_id=f"c-{cid}", outcome=outcome))
    rep1 = assess_dependence(obs, seed=42, resamples=10_000)
    rep2 = assess_dependence(obs, seed=42, resamples=10_000)
    assert rep1 == rep2
    assert rep1.guarantee_label == "approximately_valid_conservative_under_rho_upper"
    assert rep1.effective_sample_size <= rep1.raw_opportunity
    assert rep1.conservative_failure_upper >= rep1.naive_cp_upper
    assert rep1.conservative_confidence_lower == pytest.approx(1.0 - rep1.conservative_failure_upper, abs=1e-9)
