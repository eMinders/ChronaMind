"""Independent rational oracles for the aggregation's single rounding points."""

from fractions import Fraction
from itertools import product

from evaluation.stats.arithmetic import exact_mean, paired_run_difference


def test_means_match_fraction_oracle_including_cancellation_and_subnormals() -> None:
    values = (-1.0, -0.1, -5e-324, 0.0, 5e-324, 0.1, 1 / 3, 1.0)
    for size in (1, 2, 3):
        for rows in product(values, repeat=size):
            expected = float(sum(map(Fraction.from_float, rows), Fraction(0)) / size)
            assert exact_mean(rows) == expected


def test_paired_run_means_match_rational_difference_and_permuted_ties() -> None:
    rows = tuple(product((0.0, 5e-324, 1 / 3, 1.0), repeat=3))
    for proposed in rows:
        proposed_mean = sum(map(Fraction.from_float, proposed), Fraction(0)) / 3
        for comparator in rows:
            comparator_mean = sum(map(Fraction.from_float, comparator), Fraction(0)) / 3
            assert paired_run_difference(proposed, comparator) == float(proposed_mean - comparator_mean)
        assert paired_run_difference(proposed, proposed[::-1]) == 0
