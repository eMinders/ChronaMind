"""Tests for exact discrete power and sample-size calculations (``evaluation/stats/power.py``, ``V2-D118``)."""

from __future__ import annotations

import math
import time
from typing import cast

import pytest
from scipy.stats import binom, binomtest, fisher_exact

from evaluation.stats.power import (
    Alternative,
    exact_binomial_power,
    exact_mcnemar_power,
    exact_two_proportion_power,
    required_n_binomial,
    required_n_mcnemar,
    required_n_two_proportion,
)


def _brute_force_binomial_power(n: int, p1: float, p0: float, alpha: float, alternative: Alternative) -> float:
    """Elementary integer-combinatorial oracle using equal-tailed rejection for two-sided."""
    total = 0.0
    for k in range(n + 1):
        p_null_le = sum(math.comb(n, j) * (p0**j) * ((1.0 - p0) ** (n - j)) for j in range(0, k + 1))
        p_null_ge = sum(math.comb(n, j) * (p0**j) * ((1.0 - p0) ** (n - j)) for j in range(k, n + 1))
        if alternative == "greater":
            reject = p_null_ge <= alpha + 1e-12
        elif alternative == "less":
            reject = p_null_le <= alpha + 1e-12
        else:
            reject = (p_null_le <= alpha / 2.0 + 1e-12) or (p_null_ge <= alpha / 2.0 + 1e-12)
        if reject:
            total += math.comb(n, k) * (p1**k) * ((1.0 - p1) ** (n - k))
    return total


def _brute_force_fisher_power(n1: int, n2: int, p1: float, p2: float, alpha: float, alternative: Alternative) -> float:
    """Exact table-by-table oracle against ``scipy.stats.fisher_exact``."""
    total = 0.0
    for x1 in range(n1 + 1):
        w1 = float(binom.pmf(x1, n1, p1))
        if w1 == 0.0:
            continue
        for x2 in range(n2 + 1):
            w2 = float(binom.pmf(x2, n2, p2))
            if w2 == 0.0:
                continue
            _, pval = cast(
                tuple[float, float],
                fisher_exact([[x1, n1 - x1], [x2, n2 - x2]], alternative=alternative),
            )
            if pval <= alpha + 1e-12:
                total += w1 * w2
    return total


def _brute_force_mcnemar_power(n: int, p10: float, p01: float, alpha: float, alternative: Alternative) -> float:
    """Exact conditional-binomial sum oracle using ``scipy.stats.binomtest``."""
    p_disc = p10 + p01
    if p_disc == 0.0:
        return 0.0
    cond = p10 / p_disc
    total = 0.0
    for m in range(1, n + 1):
        pm = math.comb(n, m) * (p_disc**m) * ((1.0 - p_disc) ** (n - m))
        cond_pow = 0.0
        for k in range(m + 1):
            if float(binomtest(k, m, 0.5, alternative=alternative).pvalue) <= alpha + 1e-12:
                cond_pow += math.comb(m, k) * (cond**k) * ((1.0 - cond) ** (m - k))
        total += pm * cond_pow
    return total


@pytest.mark.parametrize("n", [1, 8, 12, 20])
@pytest.mark.parametrize("alternative", ["two-sided", "greater", "less"])
@pytest.mark.parametrize(("p1", "p0"), [(0.0, 0.125), (0.5, 0.5), (0.85, 0.5), (1.0, 0.85)])
def test_exact_binomial_power_matches_elementary_combinatorial_oracle(
    n: int, p1: float, p0: float, alternative: Alternative
) -> None:
    expected = _brute_force_binomial_power(n, p1, p0, 0.05, alternative)
    actual = exact_binomial_power(n, p1, p0, alpha=0.05, alternative=alternative)
    assert actual == pytest.approx(expected, rel=0, abs=1e-11)


@pytest.mark.parametrize(("n1", "n2"), [(1, 1), (5, 10), (8, 8), (11, 11), (12, 12)])
@pytest.mark.parametrize("alternative", ["two-sided", "greater", "less"])
@pytest.mark.parametrize(("p1", "p2"), [(0.0, 0.125), (0.5, 0.5), (10 / 12, 1.0), (7 / 11, 10 / 11)])
def test_exact_two_proportion_power_matches_fisher_oracle(
    n1: int, n2: int, p1: float, p2: float, alternative: Alternative
) -> None:
    expected = _brute_force_fisher_power(n1, n2, p1, p2, 0.05, alternative)
    actual = exact_two_proportion_power(n1, n2, p1, p2, alpha=0.05, alternative=alternative)
    assert actual == pytest.approx(expected, rel=0, abs=1e-10)


@pytest.mark.parametrize("n", [1, 8, 11, 15])
@pytest.mark.parametrize("alternative", ["two-sided", "greater", "less"])
@pytest.mark.parametrize(("p10", "p01"), [(0.0, 0.0), (0.125, 0.0), (0.20, 0.05), (0.15, 0.15), (0.05, 0.25)])
def test_exact_mcnemar_power_matches_binomtest_oracle(
    n: int, p10: float, p01: float, alternative: Alternative
) -> None:
    expected = _brute_force_mcnemar_power(n, p10, p01, 0.05, alternative)
    actual = exact_mcnemar_power(n, p10, p01, alpha=0.05, alternative=alternative)
    assert actual == pytest.approx(expected, rel=0, abs=1e-11)


@pytest.mark.parametrize(
    ("p1", "p0", "alternative"),
    [
        (0.15, 0.05, "greater"),
        (0.20, 0.10, "two-sided"),
        (0.70, 0.50, "two-sided"),
        (0.0, 0.125, "two-sided"),
        (0.0, 0.125, "less"),
        (1.0, 0.85, "two-sided"),
        (1.0, 0.85, "greater"),
        (0.99, 0.90, "two-sided"),
    ],
)
def test_required_n_binomial_matches_brute_force_even_on_sawtooth_regimes(
    p1: float, p0: float, alternative: Alternative
) -> None:
    expected = next(n for n in range(1, 500) if exact_binomial_power(n, p1, p0, 0.05, alternative) >= 0.80)
    actual = required_n_binomial(p1, p0, 0.80, alpha=0.05, alternative=alternative)
    assert actual == expected
    assert exact_binomial_power(actual, p1, p0, 0.05, alternative) >= 0.80
    if actual > 1:
        assert exact_binomial_power(actual - 1, p1, p0, 0.05, alternative) < 0.80


@pytest.mark.parametrize(
    ("p1", "p2", "alternative"),
    [
        (10 / 12, 1.0, "two-sided"),
        (17 / 20, 1.0, "two-sided"),
        (7 / 11, 10 / 11, "two-sided"),
        (0.0, 0.125, "two-sided"),
        (0.40, 0.60, "two-sided"),
        (0.90, 0.99, "two-sided"),
        (0.90, 0.99, "less"),
    ],
)
def test_required_n_two_proportion_matches_brute_force_at_small_and_medium_n(
    p1: float, p2: float, alternative: Alternative
) -> None:
    expected = next(n for n in range(1, 250) if exact_two_proportion_power(n, n, p1, p2, 0.05, alternative) >= 0.80)
    actual = required_n_two_proportion(p1, p2, 0.80, alpha=0.05, alternative=alternative)
    assert actual == expected
    assert exact_two_proportion_power(actual, actual, p1, p2, 0.05, alternative) >= 0.80
    if actual > 1:
        assert exact_two_proportion_power(actual - 1, actual - 1, p1, p2, 0.05, alternative) < 0.80


@pytest.mark.parametrize(
    ("p10", "p01", "alternative"),
    [
        (2 / 12, 0.0, "two-sided"),
        (3 / 20, 0.0, "two-sided"),
        (3 / 11, 0.0, "two-sided"),
        (1 / 8, 0.0, "two-sided"),
        (0.20, 0.05, "two-sided"),
        (0.20, 0.05, "greater"),
        (0.05, 0.20, "less"),
    ],
)
def test_required_n_mcnemar_matches_brute_force(p10: float, p01: float, alternative: Alternative) -> None:
    expected = next(n for n in range(1, 250) if exact_mcnemar_power(n, p10, p01, 0.05, alternative) >= 0.80)
    actual = required_n_mcnemar(p10, p01, 0.80, alpha=0.05, alternative=alternative)
    assert actual == expected
    assert exact_mcnemar_power(actual, p10, p01, 0.05, alternative) >= 0.80


def test_chronamind_comparison_regimes_match_documented_power_table() -> None:
    """Pin the numbers documented in ``reports/model_selection/README.md`` and ``reports/retrieval/README.md``."""
    assert required_n_two_proportion(1.0, 10 / 12, 0.80, alpha=0.05, alternative="two-sided") == 46
    assert required_n_two_proportion(1.0, 10 / 12, 0.80, alpha=0.05, alternative="greater") == 39
    assert required_n_two_proportion(1.0, 17 / 20, 0.80, alpha=0.05, alternative="two-sided") == 52
    assert required_n_two_proportion(1.0, 17 / 20, 0.80, alpha=0.05, alternative="greater") == 44
    assert required_n_two_proportion(10 / 11, 7 / 11, 0.80, alpha=0.05, alternative="two-sided") == 40
    assert required_n_two_proportion(10 / 11, 7 / 11, 0.80, alpha=0.05, alternative="greater") == 34
    assert required_n_two_proportion(0.0, 1 / 8, 0.80, alpha=0.05, alternative="two-sided") == 62
    assert required_n_two_proportion(0.0, 1 / 8, 0.80, alpha=0.05, alternative="less") == 53
    assert required_n_two_proportion(0.0, 1 / 12, 0.80, alpha=0.05, alternative="two-sided") == 94
    assert required_n_mcnemar(3 / 11, 0.0, 0.80, alpha=0.05, alternative="two-sided") == 28
    assert required_n_mcnemar(3 / 11, 0.0, 0.80, alpha=0.05, alternative="greater") == 24
    assert required_n_binomial(0.0, 0.125, 0.80, alpha=0.05, alternative="two-sided") == 28
    assert required_n_binomial(0.0, 0.125, 0.80, alpha=0.05, alternative="less") == 23
    assert exact_binomial_power(8, 0.0, 0.125, alpha=0.05, alternative="two-sided") == 0.0


def test_power_monotonicity_in_effect_size_and_alpha() -> None:
    p_small = exact_two_proportion_power(50, 50, 0.50, 0.60, alpha=0.05)
    p_large = exact_two_proportion_power(50, 50, 0.50, 0.80, alpha=0.05)
    assert p_small < p_large

    p_strict = exact_binomial_power(40, 0.70, 0.50, alpha=0.01)
    p_loose = exact_binomial_power(40, 0.70, 0.50, alpha=0.10)
    assert p_strict <= p_loose

    # Under the exact null, power must never exceed alpha
    assert exact_binomial_power(30, 0.50, 0.50, alpha=0.05) <= 0.05
    assert exact_two_proportion_power(30, 30, 0.50, 0.50, alpha=0.05) <= 0.05
    assert exact_mcnemar_power(30, 0.15, 0.15, alpha=0.05) <= 0.05


def test_required_n_queries_run_in_sub_second_time() -> None:
    """Ensure binary search + vectorized sums handle large-N small-effect queries fast without O(N^3) loops."""
    started = time.process_time()
    n_bin = required_n_binomial(0.52, 0.50, 0.80, alpha=0.05)
    n_two_small = required_n_two_proportion(0.90, 0.99, 0.80, alpha=0.05)
    n_two_med = required_n_two_proportion(0.50, 0.58, 0.80, alpha=0.05)
    n_mcn = required_n_mcnemar(0.12, 0.08, 0.80, alpha=0.05)
    elapsed = time.process_time() - started

    assert n_bin > 4000
    assert n_two_small == 106
    assert n_two_med > 500
    assert n_mcn > 900
    # ~0.7 s of CPU on an idle machine; an O(N^3) regression takes minutes at these N, so 5 s separates the two with
    # room for a loaded CI runner (the 1.0 s budget this replaced failed at 1.76 s under `pytest -n auto`).
    assert elapsed < 5.0, f"required-N suite took {elapsed:.3f}s"


def test_invalid_inputs_and_unreachable_targets_raise_value_error() -> None:
    for bad_n in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            exact_binomial_power(bad_n, 0.7, 0.5)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            exact_two_proportion_power(bad_n, 10, 0.7, 0.5)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            exact_mcnemar_power(bad_n, 0.2, 0.1)  # type: ignore[arg-type]

    for bad_p in (-0.1, 1.1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            exact_binomial_power(10, bad_p, 0.5)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            exact_two_proportion_power(10, 10, bad_p, 0.5)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            exact_mcnemar_power(10, bad_p, 0.1)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        exact_binomial_power(10, 0.5, 0.0)
    with pytest.raises(ValueError):
        exact_mcnemar_power(10, 0.6, 0.5)
    with pytest.raises(ValueError):
        required_n_binomial(0.5, 0.5, 0.80)
    with pytest.raises(ValueError):
        required_n_binomial(0.3, 0.5, 0.80, alternative="greater")
    with pytest.raises(ValueError):
        required_n_two_proportion(0.5, 0.5, 0.80)
    with pytest.raises(ValueError):
        required_n_two_proportion(0.5, 0.7, 0.80, ratio=0.0)
    with pytest.raises(ValueError):
        required_n_mcnemar(0.0, 0.0, 0.80)
    with pytest.raises(ValueError):
        required_n_mcnemar(0.1, 0.2, 0.80, alternative="greater")
    with pytest.raises(ValueError):
        exact_binomial_power(10, 0.7, 0.5, alternative="bad")  # type: ignore[arg-type]
