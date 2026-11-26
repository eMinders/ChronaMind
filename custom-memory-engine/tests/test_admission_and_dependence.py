"""Unit tests for SD-002 admission, FWER closed testing, FAR_tau, and SD-008 dependence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

from custom_memory_engine.soundness.admission import Ledger, assess
from evaluation.stats.admission import (
    DEFAULT_TAU_CONFIDENCE,
    DEFAULT_TAU_COVERAGE,
    MIN_REFUTATION_ATTEMPTS,
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

_MIN_ZERO_COUNTER_FAILING_AT_97_5 = 29


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
    # Multiplicity control: at a tight family-wise level (0.025) the borderline pair's Holm-adjusted p (0.04506)
    # no longer clears, although each still passes its per-abstraction bound.
    outcomes = closed_testing_admit(candidates, fwer_alpha=0.025)
    by_id = {o.candidate_id: o for o in outcomes}
    assert by_id["c-strong"].admissible is True
    assert by_id["c-borderline-1"].admissible is False  # multiplicity adjustment across 2 remaining hypotheses
    assert by_id["c-borderline-2"].admissible is False
    assert by_id["c-untested"].admissible is False

    # At the specification's FWER 0.05 (STATISTICS.md §5.4.3) the borderline pair passes both the per-abstraction
    # bound (0.9026 >= 0.90 at one-sided 97.5%) and Holm (0.04506 <= 0.05).
    default_by_id = {o.candidate_id: o for o in closed_testing_admit(candidates)}
    assert default_by_id["c-strong"].admissible is True
    assert default_by_id["c-borderline-1"].admissible is True
    assert default_by_id["c-borderline-2"].admissible is True
    assert default_by_id["c-untested"].admissible is False

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


def test_the_family_wise_level_does_not_move_the_per_abstraction_bound() -> None:
    candidate = ClosedTestingCandidate(
        candidate_id="c-29",
        support=_MIN_ZERO_COUNTER_FAILING_AT_97_5,
        counter=0,
        undetermined=0,
        refutation_attempts=3,
        negative_control_clean=True,
    )
    (outcome,) = closed_testing_admit((candidate,), fwer_alpha=0.05)
    assert outcome.admissible is False
    assert "confidence_lower_below_threshold" in outcome.reasons
    expected = 1.0 - cp_upper(0, _MIN_ZERO_COUNTER_FAILING_AT_97_5)  # validation default: one-sided 97.5%
    assert outcome.assessment.ledger.confidence_lower == pytest.approx(expected, abs=1e-12)
    assert outcome.assessment.ledger.confidence_lower == pytest.approx(0.8806, abs=1e-4)
    for alpha in (0.01, 0.025, 0.05, 0.10):
        (varied,) = closed_testing_admit((candidate,), fwer_alpha=alpha)
        assert varied.assessment.ledger.confidence_lower == outcome.assessment.ledger.confidence_lower


def test_admission_requires_refutation_and_negative_control_evidence_to_be_supplied() -> None:
    unchecked = cast(Callable[..., Any], assess_admission)
    with pytest.raises(TypeError):
        unchecked(40, 0, 0)
    with pytest.raises(TypeError):
        unchecked(40, 0, 0, refutation_attempts=3)
    with pytest.raises(TypeError):
        unchecked(40, 0, 0, negative_control_clean=True)

    untested = assess_admission(40, 0, 0, refutation_attempts=0, negative_control_clean=True)
    assert untested.admissible is False
    assert untested.reasons == ("too_few_refutation_attempts",)
    dirty = assess_admission(40, 0, 0, refutation_attempts=3, negative_control_clean=False)
    assert dirty.admissible is False
    assert dirty.reasons == ("negative_control_slice_not_clean",)
    control = assess_admission(40, 0, 0, refutation_attempts=3, negative_control_clean=True)
    assert control.admissible is True


def _all_support_observations(n_clusters: int, cluster_size: int) -> list[ClusterObservation]:
    return [
        ClusterObservation(cluster_id=f"c-{cid}", outcome="support")
        for cid in range(n_clusters)
        for _ in range(cluster_size)
    ]


def test_a_dependence_report_below_tau_blocks_admission_that_the_raw_bound_allows() -> None:
    report = assess_dependence(_all_support_observations(10, 4), seed=3, resamples=10_000)
    assert report.conservative_confidence_lower < DEFAULT_TAU_CONFIDENCE
    raw = assess_admission(40, 0, 0, refutation_attempts=3, negative_control_clean=True)
    assert raw.admissible is True

    gated = assess_admission(40, 0, 0, refutation_attempts=3, negative_control_clean=True, dependence=report)
    assert gated.admissible is False
    assert gated.reasons == ("dependence_conservative_confidence_below_threshold",)
    assert gated.dependence_confidence_lower == report.conservative_confidence_lower


def test_a_dependence_report_above_tau_adds_no_reason() -> None:
    report = assess_dependence(_all_support_observations(100, 1), seed=3, resamples=10_000)
    assert report.conservative_confidence_lower >= DEFAULT_TAU_CONFIDENCE
    gated = assess_admission(100, 0, 0, refutation_attempts=3, negative_control_clean=True, dependence=report)
    assert gated.admissible is True
    assert "dependence_conservative_confidence_below_threshold" not in gated.reasons
    assert gated.dependence_confidence_lower == report.conservative_confidence_lower

    without = assess_admission(100, 0, 0, refutation_attempts=3, negative_control_clean=True)
    assert without.dependence_confidence_lower is None
    assert without.admissible is True


def test_a_dependence_report_for_a_different_ledger_is_refused() -> None:
    report = assess_dependence(_all_support_observations(10, 4), seed=3, resamples=10_000)
    with pytest.raises(ValueError, match="^dependence_report_ledger_mismatch$"):
        assess_admission(41, 0, 0, refutation_attempts=3, negative_control_clean=True, dependence=report)
    with pytest.raises(ValueError, match="^dependence_report_ledger_mismatch$"):
        assess_admission(39, 1, 0, refutation_attempts=3, negative_control_clean=True, dependence=report)


def test_the_evaluation_admission_predicate_equals_the_engine_admission_predicate() -> None:
    tuples = 0
    for s in range(41):
        for k in range(11):
            for u in (0, 5, 45):
                o = s + k + u
                if o == 0 or k + u > o:
                    continue
                tuples += 1
                evaluation = assess_admission(
                    s, k, u, refutation_attempts=MIN_REFUTATION_ATTEMPTS, negative_control_clean=True
                )
                engine = assess(
                    Ledger(s, k, u), refutation_attempts=MIN_REFUTATION_ATTEMPTS, negative_control_clean=True
                )
                assert evaluation.admissible == engine.admissible, (s, k, u)
                assert evaluation.ledger.confidence_lower == pytest.approx(engine.confidence_lower, abs=1e-12), (
                    s,
                    k,
                    u,
                )
                assert ("coverage_below_threshold" in evaluation.reasons) == (
                    "coverage_below_threshold" in engine.reasons
                ), (s, k, u)
                assert evaluation.ledger.coverage == pytest.approx(engine.coverage, abs=1e-12), (s, k, u)
    assert tuples == 41 * 11 * 3 - 1
