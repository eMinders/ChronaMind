"""Holm values checked against closed-testing and small analytical references."""

from fractions import Fraction
from itertools import combinations, permutations, product
from random import Random
from types import MappingProxyType
from typing import cast

import pytest

from evaluation.stats.multiplicity import confirmatory_holm, holm_adjust


def closed_bonferroni_adjustment(values: tuple[Fraction, ...]) -> tuple[float, ...]:
    """Independent oracle: test every intersection containing a given hypothesis."""
    result: list[float] = []
    for target in range(len(values)):
        intersection_p_values = (
            len(subset) * min(values[index] for index in subset)
            for size in range(1, len(values) + 1)
            for subset in combinations(range(len(values)), size)
            if target in subset
        )
        result.append(float(min(Fraction(1), max(intersection_p_values))))
    return tuple(result)


def test_step_down_preserves_previous_maximum_and_clamps() -> None:
    # Sorted products are .03, .06, .04; the last must retain the earlier .06.
    assert holm_adjust({"first": 0.01, "last": 0.04, "middle": 0.03}) == pytest.approx(
        {"first": 0.03, "last": 0.06, "middle": 0.06}
    )
    assert holm_adjust({"a": 0.6, "b": 0.7, "c": 0.8}) == {"a": 1.0, "b": 1.0, "c": 1.0}


def test_equal_raw_p_values_receive_equal_adjusted_values() -> None:
    assert holm_adjust({"a": 0.01, "b": 0.01, "c": 0.20, "d": 0.04}) == pytest.approx(
        {"a": 0.04, "b": 0.04, "c": 0.20, "d": 0.08}
    )


def test_endpoints_singleton_and_readonly_mapping() -> None:
    assert holm_adjust({"only": 0.02}) == {"only": 0.02}
    original = {"a": 0, "b": 1, "c": 0.01}
    assert holm_adjust(MappingProxyType(original)) == {"a": 0.0, "b": 1.0, "c": 0.02}
    assert original == {"a": 0, "b": 1, "c": 0.01}
    assert holm_adjust({"a": 0, "b": 0}) == {"a": 0.0, "b": 0.0}
    assert holm_adjust({"a": 1, "b": 1}) == {"a": 1.0, "b": 1.0}


def test_adjustments_do_not_depend_on_chosen_significance_level() -> None:
    data = {"a": 0.01, "b": 0.04, "c": 0.20}
    assert holm_adjust(data, alpha=0.01) == holm_adjust(data, alpha=0.10)


def test_insertion_order_does_not_change_values_or_mutate_input() -> None:
    entries = (("h1", 0.01), ("h2", 0.03), ("h3", 0.03), ("h4", 0.9))
    expected = holm_adjust(dict(entries))
    for ordering in permutations(entries):
        original = dict(ordering)
        result = holm_adjust(original)
        assert result == expected
        assert tuple(result) == tuple(original)
        assert tuple(original.items()) == ordering


def test_holm_matches_closed_intersection_tests_for_exhaustive_small_families() -> None:
    grid = (Fraction(0), Fraction(1, 100), Fraction(3, 100), Fraction(1, 2), Fraction(1))
    for size in range(1, 5):
        for values in product(grid, repeat=size):
            observed = holm_adjust({str(index): float(value) for index, value in enumerate(values)})
            assert tuple(observed.values()) == pytest.approx(closed_bonferroni_adjustment(values), abs=1e-15)


def test_seeded_family_properties_and_larger_closed_test_oracle() -> None:
    rng = Random(34005)
    for _ in range(40):
        values = tuple(Fraction(rng.randrange(1001), 1000) for _ in range(rng.randrange(2, 7)))
        raw = {str(index): float(value) for index, value in enumerate(values)}
        adjusted = holm_adjust(raw)
        assert tuple(adjusted.values()) == pytest.approx(closed_bonferroni_adjustment(values), abs=1e-15)
        for key in raw:
            assert raw[key] <= adjusted[key] <= 1
        order = sorted(raw, key=lambda key: raw[key])
        assert [adjusted[key] for key in order] == sorted(adjusted.values())
        # Closed testing is monotone when any raw p-value becomes less persuasive.
        weakened = dict(raw)
        key = str(rng.randrange(len(values)))
        weakened[key] = min(1.0, raw[key] + 0.1)
        weakened_adjustment = holm_adjust(weakened)
        assert all(weakened_adjustment[label] >= adjusted[label] for label in raw)


def test_confirmatory_family_has_three_members_and_h2_uses_raw_maximum() -> None:
    result = confirmatory_holm(h1_history=0.01, h2_dense=0.001, h2_lexical=0.04, h3_asr=0.03)
    assert tuple(result) == ("H1", "H2", "H3")
    assert result == pytest.approx({"H1": 0.03, "H2": 0.06, "H3": 0.06})
    assert result == confirmatory_holm(h1_history=0.01, h2_dense=0.04, h2_lexical=0.001, h3_asr=0.03)


def test_h2_success_requires_both_contrasts_and_unavailable_p_one_keeps_family() -> None:
    result = confirmatory_holm(h1_history=0.001, h2_dense=0.001, h2_lexical=1.0, h3_asr=0.02)
    assert result == pytest.approx({"H1": 0.003, "H2": 1.0, "H3": 0.04})


@pytest.mark.parametrize(
    "invalid", [True, False, -0.001, 1.001, float("nan"), float("inf"), -float("inf"), "0.01", None]
)
def test_invalid_probability_is_rejected(invalid: object) -> None:
    with pytest.raises(ValueError):
        holm_adjust({"invalid": cast(float, invalid)})


@pytest.mark.parametrize("invalid", [True, False, 0.0, 1.0, -0.1, 1.1, float("nan"), float("inf"), "0.05", None])
def test_invalid_alpha_is_rejected(invalid: object) -> None:
    with pytest.raises(ValueError):
        holm_adjust({"valid": 0.01}, alpha=cast(float, invalid))


def test_empty_family_is_rejected() -> None:
    with pytest.raises(ValueError):
        holm_adjust({})


@pytest.mark.parametrize("argument", ["h1_history", "h2_dense", "h2_lexical", "h3_asr"])
@pytest.mark.parametrize("invalid", [True, -0.1, 1.1, float("nan"), float("inf")])
def test_each_confirmatory_probability_is_validated_before_h2_max(argument: str, invalid: float) -> None:
    # In particular max(valid, nan) can hide nan depending on argument ordering.
    values = dict(h1_history=0.01, h2_dense=0.02, h2_lexical=0.03, h3_asr=0.04)
    values[argument] = invalid
    with pytest.raises(ValueError):
        confirmatory_holm(**values)
