"""Analytical cluster, pairing, run-aggregation and percentile oracles."""

from fractions import Fraction
from itertools import permutations
from typing import cast

import numpy as np
import pytest
from pydantic import ValidationError

from evaluation.stats.bootstrap import cluster_bootstrap
from evaluation.stats.models import Dataset, Observation, PairedObservation, PairedSample, pair_observations


def sample(values: tuple[float, ...], *, dataset: Dataset = "T") -> PairedSample:
    return PairedSample(
        dataset=dataset,
        endpoint="recall" if dataset == "LongMemEval_S" else "history_taa",
        comparator="A2",
        observations=tuple(
            PairedObservation(
                cluster_id=str(i), item_id=str(i), run_id="r0", proposed=max(v, 0), comparator=max(-v, 0)
            )
            for i, v in enumerate(values)
        ),
    )


def test_cluster_equal_weight_and_whole_cluster_percentiles() -> None:
    # One positive item in one scenario; nine negative items in another. Pooling
    # questions gives -0.8 instead of 0, and pseudo-resampling gives a narrow CI.
    rows = (PairedObservation(cluster_id="a", item_id="a0", run_id="r", proposed=1.0, comparator=0.0),) + tuple(
        PairedObservation(cluster_id="b", item_id=f"b{i}", run_id="r", proposed=0.0, comparator=1.0) for i in range(9)
    )
    result = cluster_bootstrap(
        PairedSample(dataset="T", endpoint="history_taa", comparator="A2", observations=rows), seed=42
    )
    assert result.estimate == 0
    assert (result.ci95.low, result.ci95.high) == (-1, 1)
    assert (result.conservative_ci.low, result.conservative_ci.high) == (-1, 1)
    assert [c.item_count for c in result.clusters] == [1, 9]
    assert result.resamples == 10_000


def test_exact_percentiles_of_independent_seeded_cluster_index_oracle() -> None:
    data = sample((-0.7, -0.1, 0.2, 0.5, 0.8))
    result = cluster_bootstrap(data, seed=813, resamples=10_011)
    # Draw all cluster identities at once, independently of production batching.
    rng = np.random.Generator(np.random.PCG64(813))
    indices = rng.integers(0, 5, size=(10_011, 5))
    values = np.asarray([-0.7, -0.1, 0.2, 0.5, 0.8])
    fractions = tuple(Fraction.from_float(float(value)) for value in values)
    # Exact rational oracle avoids accepting NumPy reduction roundoff as truth.
    means = [float(sum((fractions[index] for index in row), Fraction(0)) / 5) for row in indices]
    reference = np.quantile(means, [0.025, 0.975, 0.05 / 6, 1 - 0.05 / 6], method="linear")
    assert (result.ci95.low, result.ci95.high) == tuple(reference[:2])
    assert (result.conservative_ci.low, result.conservative_ci.high) == tuple(reference[2:])
    assert result.noninferiority_lower == result.ci95.low
    assert result.conservative_ci.low <= result.ci95.low <= result.ci95.high <= result.conservative_ci.high


def test_repeated_runs_are_averaged_and_never_extra_independent_units() -> None:
    rows = tuple(
        PairedObservation(cluster_id="scenario", item_id="question", run_id=str(run), proposed=value, comparator=0.0)
        for run, value in enumerate((0.0, 1.0, 0.5))
    )
    result = cluster_bootstrap(
        PairedSample(dataset="T", endpoint="history_taa", comparator="A2", observations=rows), seed=2
    )
    assert result.estimate == result.ci95.low == result.ci95.high == 0.5
    assert len(result.clusters) == 1
    assert result.clusters[0].run_count == 3
    assert result.clusters[0].item_count == 1


def test_deterministic_under_input_row_permutation() -> None:
    data = sample((0.2, -0.5, 0.7))
    expected = cluster_bootstrap(data, seed=7)
    for rows in permutations(data.observations):
        reordered = PairedSample(dataset="T", endpoint="history_taa", comparator="A2", observations=rows)
        assert cluster_bootstrap(reordered, seed=7) == expected


def test_pairing_is_identity_join_not_row_position() -> None:
    left = [
        Observation(cluster_id="s", item_id="q", run_id="0", value=1.0),
        Observation(cluster_id="s", item_id="q", run_id="1", value=0.0),
    ]
    right = [
        Observation(cluster_id="s", item_id="q", run_id="1", value=1.0),
        Observation(cluster_id="s", item_id="q", run_id="0", value=0.0),
    ]
    paired = pair_observations(left, right, dataset="T", endpoint="history_taa", comparator_id="A2")
    assert [row.run_id for row in paired.observations] == ["0", "1"]
    assert [(row.proposed, row.comparator) for row in paired.observations] == [(1, 0), (0, 1)]
    assert paired.cluster_effects()[0].difference == 0
    with pytest.raises(ValueError, match="unpaired"):
        pair_observations(left, right[:1], dataset="T", endpoint="history_taa", comparator_id="A2")
    with pytest.raises(ValueError, match="duplicate"):
        pair_observations(left + left[:1], right, dataset="T", endpoint="history_taa", comparator_id="A2")


@pytest.mark.parametrize("seed", [-1, True, 1.5, float("nan")])
def test_invalid_seed(seed: object) -> None:
    with pytest.raises(ValueError, match="seed"):
        cluster_bootstrap(sample((0.1,)), seed=cast(int, seed))


@pytest.mark.parametrize("resamples", [0, 9999, True, 10_000.0])
def test_invalid_resample_count(resamples: object) -> None:
    with pytest.raises(ValueError, match="resamples"):
        cluster_bootstrap(sample((0.1,)), seed=1, resamples=cast(int, resamples))


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), True, "0.5"])
def test_invalid_fraction(value: object) -> None:
    with pytest.raises(ValidationError):
        PairedObservation(cluster_id="s", item_id="q", run_id="r", proposed=cast(float, value), comparator=0.0)


def test_reject_incomplete_runs_duplicate_rows_and_misclustered_question() -> None:
    row = PairedObservation(cluster_id="s", item_id="q", run_id="r", proposed=0.5, comparator=0.0)
    with pytest.raises(ValueError, match="duplicate"):
        PairedSample(dataset="T", endpoint="history_taa", comparator="A2", observations=(row, row))
    other = PairedObservation(cluster_id="s", item_id="q2", run_id="different", proposed=0.5, comparator=0.0)
    with pytest.raises(ValueError, match="run_grid"):
        PairedSample(dataset="T", endpoint="history_taa", comparator="A2", observations=(row, other))
    with pytest.raises(ValueError, match="question_must_be_its_cluster"):
        PairedSample(dataset="LongMemEval_S", endpoint="recall", comparator="A5", observations=(row,))
    with pytest.raises(ValueError, match="dataset_mismatch"):
        PairedSample(dataset="G", endpoint="history_taa", comparator="A2", observations=(row,))


def test_longmemeval_uses_independent_questions_and_json_roundtrip() -> None:
    data = sample((-0.5, 0.5), dataset="LongMemEval_S")
    assert data.analysis_unit == "question"
    assert len(data.cluster_effects()) == 2
    assert PairedSample.model_validate_json(data.model_dump_json()) == data
