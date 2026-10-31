"""B-06 / V2-D40: the bound gates verification, not creation — with the guarantee unchanged."""

import itertools

import pytest

from custom_memory_engine.soundness.admission import (
    AdmissionError,
    Ledger,
    assess,
    cp_upper,
    creation_outcome,
    may_verify,
)
from evaluation.stats.clopper_pearson import cp_upper as evaluation_cp_upper
from validation.cp_bounds import cp_upper as validation_cp_upper

GRID = [(k, n) for n in (1, 2, 5, 8, 12, 29, 35, 36, 40, 50, 100, 500) for k in range(0, min(n, 12) + 1)]


@pytest.mark.parametrize(("k", "n"), GRID)
def test_engine_bound_equals_both_reference_implementations(k: int, n: int) -> None:
    engine = cp_upper(k, n)
    assert abs(engine - evaluation_cp_upper(k, n)) < 1e-12
    assert abs(engine - validation_cp_upper(k, n)) < 1e-9


@pytest.mark.parametrize(("o", "expected"), [(8, 0.3694), (29, 0.1194), (36, 0.0974), (40, 0.0881), (100, 0.0362)])
def test_statistics_section_4_2_table(o: int, expected: float) -> None:
    assert abs(cp_upper(0, o) - expected) < 1e-4


def clean(**overrides: object):
    kwargs = {"refutation_attempts": 3, "negative_control_clean": True} | overrides
    return kwargs


def test_zero_counter_boundary_is_thirty_six_at_tau_090() -> None:
    assert not assess(Ledger(35, 0, 0), **clean()).admissible
    result = assess(Ledger(36, 0, 0), **clean())
    assert result.admissible and result.rendering == "≤ 9.7% failure rate at 97.5% confidence"
    assert "zero_counter_opportunity_too_small" in assess(Ledger(35, 0, 0), **clean()).reasons


def test_undetermined_observations_count_as_failures_r3_regression() -> None:
    """s=5, k=0, u=45 was admitted at 0.929 when u was treated as success."""
    result = assess(Ledger(5, 0, 45), **clean())
    assert not result.admissible
    assert abs(result.confidence_lower - 0.033) < 1e-3
    assert {"confidence_lower_below_threshold", "coverage_below_threshold"} <= set(result.reasons)


def test_some_unknown_case_from_statistics_is_rejected() -> None:
    result = assess(Ledger(30, 0, 10), **clean())
    assert abs(result.confidence_lower - 0.588) < 1e-3 and not result.admissible


def test_coverage_gate_boundary_is_inclusive() -> None:
    ledger = Ledger(300, 0, 200)  # coverage exactly 0.60
    result = assess(ledger, **clean())
    assert result.coverage == pytest.approx(0.60)
    assert "coverage_below_threshold" not in result.reasons


def test_refutation_attempts_boundary() -> None:
    assert "too_few_refutation_attempts" in assess(Ledger(100, 0, 0), **clean(refutation_attempts=2)).reasons
    assert assess(Ledger(100, 0, 0), **clean(refutation_attempts=3)).admissible


def test_dirty_negative_control_blocks_admission() -> None:
    result = assess(Ledger(100, 0, 0), **clean(negative_control_clean=False))
    assert result.reasons == ("negative_control_slice_not_clean",)


def test_counters_widen_the_bound() -> None:
    assert assess(Ledger(30, 2, 8), **clean()).confidence_lower < assess(Ledger(40, 0, 0), **clean()).confidence_lower


def test_below_threshold_candidate_is_created_as_an_unverified_hypothesis() -> None:
    """The decision itself: a below-τ candidate is stored, never rejected for its bound."""
    outcome = creation_outcome(True)
    assert (outcome.lifecycle, outcome.verified, outcome.channel) == ("inferred", False, "hypotheses")
    assert assess(Ledger(8, 0, 0), **clean()).rendering == "hypothesis only"


def test_structural_failure_is_the_only_creation_rejection() -> None:
    with pytest.raises(AdmissionError):
        creation_outcome(False)


def test_verification_requires_both_admissibility_and_held_out_pass() -> None:
    strong, weak = assess(Ledger(100, 0, 0), **clean()), assess(Ledger(8, 0, 0), **clean())
    assert may_verify(strong, held_out_passed=True)
    assert not may_verify(strong, held_out_passed=False)
    assert not may_verify(weak, held_out_passed=True)


def test_evidence_accumulation_can_later_qualify_a_stored_hypothesis() -> None:
    history = [Ledger(n, 0, 0) for n in (8, 20, 35, 36)]
    verdicts = [assess(ledger, **clean()).admissible for ledger in history]
    assert verdicts == [False, False, False, True]


@pytest.mark.parametrize(
    "args",
    [(-1, 0, 0), (0, 0, 0), (1.0, 0, 0), (True, 0, 0)],
)
def test_malformed_ledgers_are_rejected(args: tuple[object, object, object]) -> None:
    with pytest.raises(AdmissionError):
        Ledger(*args)  # type: ignore[arg-type]


def test_thresholds_are_validated_and_bound_inputs_are_checked() -> None:
    with pytest.raises(AdmissionError):
        assess(Ledger(10, 0, 0), **clean(), tau_confidence=1.5)
    with pytest.raises(AdmissionError):
        cp_upper(3, 2)
    with pytest.raises(AdmissionError):
        cp_upper(0, 0)
    assert cp_upper(4, 4) == 1.0


def test_bound_is_monotone_in_failures_and_trials() -> None:
    for k, n in itertools.product(range(5), (10, 20, 40)):
        assert cp_upper(k, n) < cp_upper(k + 1, n)
        assert cp_upper(k, n + 10) < cp_upper(k, n)
