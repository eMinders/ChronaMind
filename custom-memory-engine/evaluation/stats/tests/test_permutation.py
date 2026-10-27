"""Independent discrete oracles for the paired cluster sign-flip test."""

from fractions import Fraction
from itertools import product
from math import fsum

import numpy as np
import pytest
from pydantic import ValidationError

from evaluation.stats.permutation import paired_permutation


def exact_oracle(values: tuple[float, ...]) -> tuple[int, int]:
    """Enumerate rational values of binary floats, without numerical tolerances."""
    weights = tuple(Fraction.from_float(float(value)) for value in values if value != 0)
    observed = abs(sum(weights))
    extremes = sum(
        abs(sum(sign * weight for sign, weight in zip(signs, weights, strict=True))) >= observed
        for signs in product((-1, 1), repeat=len(weights))
    )
    return extremes, 2 ** len(weights)


@pytest.mark.parametrize(
    "values",
    [
        (1.0,),
        (-1.0,),
        (0.0,),
        (0.0, -0.0, 0.0),
        (0.5, -0.25, 0.125, 0.0),
        (0.1, 0.2, -0.3),
        (1.0, -1.0, 2.0**-55),
        (1.0, -1.0 + 2.0**-52, 2.0**-54),
        (2.0**-1074, -(2.0**-1074), 2.0**-1073),
        (1.0, 2.0**-1074, -1.0),
    ],
)
def test_exact_matches_rational_enumeration(values: tuple[float, ...]) -> None:
    expected_count, total = exact_oracle(values)
    result = paired_permutation(values, seed=31)
    assert result.method == "exact"
    assert result.extreme_count == expected_count
    assert result.permutations == total
    assert result.p_value == expected_count / total
    assert result.estimate == fsum(values) / len(values)
    assert result.n_clusters == len(values)
    assert result.n_nonzero + result.n_zero == len(values)
    assert result.n_positive == sum(value > 0 for value in values)
    assert result.n_negative == sum(value < 0 for value in values)
    assert result.n_zero == sum(value == 0 for value in values)


def test_all_small_integer_grids_match_rational_oracle() -> None:
    for values in product((-1.0, -0.5, 0.0, 0.5, 1.0), repeat=3):
        count, total = exact_oracle(values)
        result = paired_permutation(values, seed=0)
        assert (result.extreme_count, result.permutations) == (count, total)


def test_exact_arithmetic_feasibility_floor_counts_nonzero_clusters() -> None:
    six = paired_permutation((1.0,) * 6 + (0.0,) * 40, seed=4)
    seven = paired_permutation((1.0,) * 7, seed=4)
    assert six.p_value == 2 / 2**6 > 0.05 / 3
    assert six.n_nonzero == 6
    assert seven.p_value == 2 / 2**7 < 0.05 / 3
    assert six.permutations == 2**6
    assert seven.permutations == 2**7


def test_direction_reversal_and_cluster_order_do_not_change_exact_p_value() -> None:
    values = (0.5, -0.25, 0.125, 0.75, 0.0)
    original = paired_permutation(values, seed=88)
    reversed_direction = paired_permutation(tuple(-value for value in values), seed=0)
    reordered = paired_permutation(tuple(reversed(values)), seed=99)
    assert original.p_value == reversed_direction.p_value == reordered.p_value
    assert original.estimate == -reversed_direction.estimate


def test_exact_enumeration_crosses_batch_boundary() -> None:
    values = (0.125,) * 10
    result = paired_permutation(values, seed=0)
    assert result.permutations == 1024
    assert result.extreme_count == 2
    assert result.p_value == 1 / 512


def test_default_exact_limit_selects_exact_or_monte_carlo() -> None:
    exact = paired_permutation((1.0,) * 16, seed=0, resamples=1)
    approximate = paired_permutation((1.0,) * 17, seed=0, resamples=1)
    assert exact.method == "exact"
    assert exact.permutations == 2**16
    assert exact.p_value == 2 / 2**16
    assert approximate.method == "monte_carlo"
    assert approximate.permutations == 1


def test_monte_carlo_matches_independent_seeded_draws_and_add_one() -> None:
    values = (0.5, -0.25, 0.125, 0.75, 0.0)
    result = paired_permutation(values, seed=23, resamples=47, max_exact_clusters=0)
    weights = np.asarray(values[:-1])
    rng = np.random.Generator(np.random.PCG64(23))
    signs = rng.integers(0, 2, size=(47, 4)) * 2 - 1
    count = int(np.count_nonzero(np.abs(signs @ weights) >= abs(fsum(values))))
    assert result.method == "monte_carlo"
    assert result.extreme_count == count
    assert result.permutations == 47
    assert result.p_value == (count + 1) / 48
    assert result.seed == 23
    assert result == paired_permutation(values, seed=23, resamples=47, max_exact_clusters=0)


def test_monte_carlo_p_value_never_zero() -> None:
    result = paired_permutation((1.0,) * 100, seed=7, resamples=3)
    assert result.extreme_count == 0
    assert result.p_value == 1 / 4


def test_monte_carlo_boundary_comparisons_match_rational_draw_oracle() -> None:
    values = (1.0, -1.0, 2.0**-55, 2.0**-54, 0.25)
    result = paired_permutation(values, seed=17, resamples=301, max_exact_clusters=0)
    rng = np.random.Generator(np.random.PCG64(17))
    signs = rng.integers(0, 2, size=(301, len(values))) * 2 - 1
    exact_values = tuple(Fraction.from_float(value) for value in values)
    observed = abs(sum(exact_values))
    count = sum(
        abs(sum(int(sign) * value for sign, value in zip(row, exact_values, strict=True))) >= observed for row in signs
    )
    assert result.extreme_count == count
    assert result.p_value == (count + 1) / 302


def test_all_zero_input_is_exact_even_when_exact_limit_is_zero() -> None:
    result = paired_permutation((0.0,) * 100, seed=13, resamples=1, max_exact_clusters=0)
    assert result.p_value == 1.0
    assert result.method == "exact"
    assert result.permutations == result.extreme_count == 1


def test_seed_does_not_mutate_numpy_global_rng() -> None:
    np.random.seed(815)
    expected = np.random.random(2)
    np.random.seed(815)
    paired_permutation((1.0, 0.5), seed=99, resamples=5, max_exact_clusters=0)
    assert np.array_equal(np.random.random(2), expected)


@pytest.mark.parametrize(
    "values", [(), (float("nan"),), (float("inf"),), (-float("inf"),), (1.01,), (-1.01,), (10**1000,), (True,), ("1",)]
)
def test_invalid_differences_are_rejected(values: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        paired_permutation(values, seed=0)  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", [True, -1, 1.5, "1"])
def test_invalid_seed_is_rejected(seed: object) -> None:
    with pytest.raises(ValueError):
        paired_permutation((0.5,), seed=seed)  # type: ignore[arg-type]


@pytest.mark.parametrize("resamples", [True, 0, -1, 1.5])
def test_invalid_resample_count_is_rejected(resamples: object) -> None:
    with pytest.raises(ValueError):
        paired_permutation((0.5,), seed=0, resamples=resamples)  # type: ignore[arg-type]


@pytest.mark.parametrize("limit", [True, -1, 21, 1.5])
def test_invalid_exact_limit_is_rejected(limit: object) -> None:
    with pytest.raises(ValueError):
        paired_permutation((0.5,), seed=0, max_exact_clusters=limit)  # type: ignore[arg-type]


def test_result_is_immutable_and_json_serializable() -> None:
    result = paired_permutation((0.5, -0.25), seed=0)
    assert '"method":"exact"' in result.model_dump_json()
    with pytest.raises(ValidationError, match="frozen"):
        result.seed = 3
