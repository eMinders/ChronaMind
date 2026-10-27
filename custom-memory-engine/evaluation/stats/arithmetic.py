"""Exact binary-input sums with one final rounding at an aggregation boundary."""

from collections.abc import Sequence


def integer_lattice(values: Sequence[float]) -> tuple[tuple[int, ...], int]:
    """Represent finite binary floats on their common power-of-two denominator."""
    if not values:
        raise ValueError("nonempty_values_required")
    ratios = tuple(value.as_integer_ratio() for value in values)
    denominator = max(divisor for _, divisor in ratios)
    return tuple(numerator * (denominator // divisor) for numerator, divisor in ratios), denominator


def exact_mean(values: Sequence[float]) -> float:
    """Round once after the exact sum/division; preserve every constant sample."""
    weights, denominator = integer_lattice(values)
    return sum(weights) / (denominator * len(weights))


def paired_run_difference(proposed: Sequence[float], comparator: Sequence[float]) -> float:
    """Average each configuration's runs before taking their paired contrast.

    Both run means stay on an exact common lattice until subtraction, avoiding
    intermediate rounding of individual P−control differences or the two means.
    """
    if len(proposed) != len(comparator) or not proposed:
        raise ValueError("equal_nonempty_run_sequences_required")
    weights, denominator = integer_lattice((*proposed, *comparator))
    count = len(proposed)
    return (sum(weights[:count]) - sum(weights[count:])) / (denominator * count)
