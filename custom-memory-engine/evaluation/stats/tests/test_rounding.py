"""Strict decision boundaries must not be crossed by intermediate rounding."""

from fractions import Fraction

import pytest

from evaluation.stats.analysis import analyze_contrast
from evaluation.stats.decisions import evaluate_h1, evaluate_h2
from evaluation.stats.models import PairedObservation, PairedSample
from evaluation.stats.multiplicity import confirmatory_holm


@pytest.mark.parametrize("count", [1, 3, 7, 9, 31, 100])
@pytest.mark.parametrize("effect", [-0.1, 0.1, -0.3, 0.3])
def test_point_mass_bootstrap_never_moves_a_strict_boundary(count: int, effect: float) -> None:
    sample = PairedSample(
        dataset="T",
        endpoint="present_taa",
        comparator="A2",
        observations=tuple(
            PairedObservation(
                cluster_id=str(i),
                item_id=str(i),
                run_id="r",
                proposed=max(effect, 0),
                comparator=max(-effect, 0),
            )
            for i in range(count)
        ),
    )
    result = analyze_contrast(sample, seed=1)
    expected = float(sum((Fraction.from_float(effect) for _ in range(count)), Fraction(0)) / count)
    assert result.bootstrap.estimate == expected
    assert result.bootstrap.ci95.low == result.bootstrap.ci95.high == expected
    assert result.bootstrap.conservative_ci.low == result.bootstrap.conservative_ci.high == expected
    assert result.bootstrap.noninferiority_lower == expected


def test_exact_run_mean_ties_cannot_become_h2_support() -> None:
    # These two run multisets have identical exact means. Rounding each paired
    # subtraction before aggregation used to manufacture a positive difference.
    proposed, comparator = (0.0, 1.0, 1 / 3), (1.0, 1 / 3, 0.0)
    assert sum(map(Fraction.from_float, proposed)) == sum(map(Fraction.from_float, comparator))
    rows = tuple(
        PairedObservation(
            cluster_id=str(i),
            item_id=str(i),
            run_id=str(run),
            proposed=p,
            comparator=c,
        )
        for i in range(8)
        for run, (p, c) in enumerate(zip(proposed, comparator, strict=True))
    )
    dense = analyze_contrast(
        PairedSample(dataset="LongMemEval_S", endpoint="recall", comparator="A5", observations=rows), seed=42
    )
    lexical = analyze_contrast(
        PairedSample(dataset="LongMemEval_S", endpoint="recall", comparator="A6", observations=rows), seed=42
    )
    assert all(cluster.difference == 0 for cluster in dense.bootstrap.clusters)
    assert dense.permutation is not None and lexical.permutation is not None
    assert dense.permutation.n_nonzero == 0
    assert dense.permutation.p_value == lexical.permutation.p_value == 1
    assert (
        evaluate_h2(dense, lexical, adjusted_p=1.0, precision_adequate=True, freeze_reference="synthetic").verdict
        != "Supported"
    )


def test_h1_exact_ni_margin_is_inconclusive_with_real_binary_scores() -> None:
    history = analyze_contrast(
        PairedSample(
            dataset="T",
            endpoint="history_taa",
            comparator="A2",
            observations=tuple(
                PairedObservation(cluster_id=str(i), item_id=f"h{i}", run_id="r", proposed=1.0, comparator=0.0)
                for i in range(7)
            ),
        ),
        seed=1,
    )
    present = analyze_contrast(
        PairedSample(
            dataset="T",
            endpoint="present_taa",
            comparator="A2",
            observations=tuple(
                PairedObservation(
                    cluster_id=str(i), item_id=f"p{i}-{j}", run_id="r", proposed=0.0, comparator=float(j == 0)
                )
                for i in range(7)
                for j in range(10)
            ),
        ),
        seed=1,
    )
    assert history.permutation is not None
    adjusted = confirmatory_holm(h1_history=history.permutation.p_value, h2_dense=1.0, h2_lexical=1.0, h3_asr=1.0)
    assert adjusted["H1"] < 0.05
    assert present.bootstrap.noninferiority_lower == -0.1
    assert (
        evaluate_h1(
            history,
            present,
            adjusted_p=adjusted["H1"],
            present_margin=0.1,
            precision_adequate=True,
            freeze_reference="synthetic",
        ).verdict
        == "Inconclusive"
    )


def test_h1_rejects_a_question_reused_in_both_exclusive_strata() -> None:
    rows = tuple(
        PairedObservation(cluster_id=str(i), item_id=f"q{i}", run_id="r", proposed=1.0, comparator=0.0)
        for i in range(7)
    )
    history = analyze_contrast(
        PairedSample(dataset="T", endpoint="history_taa", comparator="A2", observations=rows),
        seed=42,
    )
    present = analyze_contrast(
        PairedSample(dataset="T", endpoint="present_taa", comparator="A2", observations=rows),
        seed=42,
    )
    with pytest.raises(ValueError, match="both_strata"):
        evaluate_h1(
            history,
            present,
            adjusted_p=0.01,
            present_margin=0.1,
            precision_adequate=True,
            freeze_reference="synthetic",
        )


def test_exact_item_mean_ties_remain_zero_at_the_cluster_boundary() -> None:
    proposed, comparator = (0.0, 1.0, 1 / 3), (1.0, 1 / 3, 0.0)
    rows = tuple(
        PairedObservation(
            cluster_id=str(i),
            item_id=f"h{i}-{j}",
            run_id="r",
            proposed=p,
            comparator=c,
        )
        for i in range(8)
        for j, (p, c) in enumerate(zip(proposed, comparator, strict=True))
    )
    history = analyze_contrast(
        PairedSample(dataset="T", endpoint="history_taa", comparator="A2", observations=rows),
        seed=42,
    )
    assert all(cluster.difference == 0 for cluster in history.bootstrap.clusters)
    assert history.bootstrap.estimate == history.bootstrap.ci95.low == history.bootstrap.ci95.high == 0
    assert history.permutation is not None
    assert history.permutation.p_value == 1 and history.permutation.n_nonzero == 0
    assert not history.inferentially_feasible
