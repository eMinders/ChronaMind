"""Independent binomial arithmetic, published table and numerical-boundary oracles."""

from io import StringIO
from itertools import pairwise
from math import ceil, comb, expm1, fsum, isclose, log, nextafter
from pathlib import Path

import pytest
from scipy.stats import binom, binomtest

from custom_memory_engine.protocols import zero_leak_upper
from evaluation.stats.clopper_pearson import cp_interval, cp_upper
from validation.cp_bounds import cp_upper as legacy_upper

CME_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("scenarios", range(20, 101))
def test_entire_twenty_to_one_hundred_grid_matches_independent_binomial_sum(scenarios: int) -> None:
    # The legacy oracle sums exact integer binomial coefficients and bisects;
    # production uses beta inversion, so this does not duplicate its algorithm.
    for leaks in range(scenarios + 1):
        assert cp_upper(leaks, scenarios) == pytest.approx(legacy_upper(leaks, scenarios), rel=0, abs=3e-13)


@pytest.mark.parametrize(
    ("leaks", "scenarios", "expected"),
    [
        (0, 20, 0.168433470983085337064446948756148256),
        (1, 20, 0.248732762772027776705509956294799986),
        (3, 20, 0.378926826545313939728785538306022758),
        (10, 20, 0.728042150439208191074959028713053074),
        (19, 20, 0.998734910502050196305707066059099920),
        (1, 40, 0.131585858482765743818625368568073581),
        (1, 100, 0.054459385392080657386477482410156205),
        (17, 100, 0.258175410632158691979621857381618210),
        (50, 100, 0.601678870496699020563609655499739425),
        (99, 100, 0.999746853967022579366163795428631537),
    ],
)
def test_seventy_digit_decimal_binomial_bisection_anchors(leaks: int, scenarios: int, expected: float) -> None:
    # Constants were derived externally with Decimal(precision=70), integer
    # comb(n,k) and 240 bisections of P(X <= k) = .025, without SciPy.
    assert cp_upper(leaks, scenarios) == pytest.approx(expected, rel=0, abs=2e-14)


def test_library_bounds_reproduce_the_complete_canonical_formatted_output() -> None:
    output = StringIO()
    print("zero observed leaks: exact upper limit of the two-sided 95% interval", file=output)
    print("   n   closed form 1-0.025^(1/n)   bisection", file=output)
    for n in (10, 20, 30, 36, 50, 100, 200, 400):
        print(f"{n:4d}   {1 - 0.025 ** (1 / n):.4f}                     {cp_upper(0, n):.4f}", file=output)
    print("\nscenarios needed for a zero-leak result to give an upper limit <= u", file=output)
    for upper in (0.20, 0.15, 0.10, 0.05):
        n = ceil(log(0.025) / log(1 - upper))
        assert cp_upper(0, n) <= upper < cp_upper(0, n - 1)
        print(f"  u = {upper:.2f}: n >= {n}", file=output)
    print("\nillustration of clustering: 12 deletion events per scenario, 0 leaks", file=output)
    for scenarios in (10, 20, 40):
        events = 12 * scenarios
        print(
            f"  scenarios={scenarios:3d} events={events:4d}  scenario-level bound={cp_upper(0, scenarios):.3f}  "
            f"event-level bound (independence assumed)={cp_upper(0, events):.3f}",
            file=output,
        )
    print("\none leaking scenario observed: upper limit of the two-sided 95% interval", file=output)
    for n in (20, 40, 100):
        print(f"  1 of {n:3d} scenarios: {cp_upper(1, n):.3f}", file=output)
    assert output.getvalue() == (CME_ROOT / "validation/cp_bounds.output.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(("leaks", "scenarios"), [(0, 1), (1, 1), (0, 36), (1, 20), (17, 100), (99, 100)])
@pytest.mark.parametrize("confidence", [0.50, 0.90, 0.95, 0.99])
def test_two_sided_interval_matches_binomial_test_root_solver(leaks: int, scenarios: int, confidence: float) -> None:
    reference = binomtest(leaks, scenarios).proportion_ci(confidence_level=confidence, method="exact")
    actual = cp_interval(leaks, scenarios, confidence=confidence)
    assert actual == pytest.approx((reference.low, reference.high), rel=0, abs=3e-12)
    tail = 0.025 if confidence == 0.95 else (1 - confidence) / 2
    assert actual[1] == cp_upper(leaks, scenarios, tail_probability=tail)


@pytest.mark.parametrize(("leaks", "scenarios"), [(1, 20), (10, 20), (19, 20), (17, 100), (499999, 1000000)])
@pytest.mark.parametrize("tail", [0.001, 0.025, 0.10])
def test_upper_bound_inverts_the_independent_binomial_cdf(leaks: int, scenarios: int, tail: float) -> None:
    upper = cp_upper(leaks, scenarios, tail_probability=tail)
    assert binom.cdf(leaks, scenarios, upper) == pytest.approx(tail, rel=3e-8, abs=1e-12)


def test_zero_and_all_leak_edges_large_counts_and_tiny_tails_are_stable() -> None:
    assert cp_interval(0, 1) == pytest.approx((0, 0.975), rel=0, abs=2e-14)
    assert cp_interval(1, 1) == pytest.approx((0.025, 1), rel=0, abs=2e-14)
    for scenarios in (1, 20, 100, 10**6, 10**16):
        assert cp_upper(scenarios, scenarios) == 1.0
        assert cp_upper(0, scenarios) == zero_leak_upper(scenarios)
        assert cp_upper(0, scenarios) == -expm1(log(0.025) / scenarios)
    # Computing BetaPPF(1-tail) would round 1-1e-20 to one and incorrectly
    # return 1; complementary inversion retains the nondegenerate endpoint.
    tiny_tail = cp_upper(1, 100, tail_probability=1e-20)
    assert 0 < tiny_tail < 1
    assert binom.cdf(1, 100, tiny_tail) == pytest.approx(1e-20, rel=1e-10, abs=0)
    narrow = cp_interval(1, 100, confidence=nextafter(1.0, 0.0))
    assert 0 < narrow[0] < narrow[1] < 1


def test_bounds_are_monotone_and_intervals_obey_binomial_symmetry() -> None:
    for scenarios in (1, 20, 100):
        uppers = [cp_upper(leaks, scenarios) for leaks in range(scenarios + 1)]
        assert all(a < b for a, b in pairwise(uppers))
        for leaks in range(scenarios + 1):
            low, high = cp_interval(leaks, scenarios)
            reflected_low, reflected_high = cp_interval(scenarios - leaks, scenarios)
            assert isclose(low, 1 - reflected_high, rel_tol=0, abs_tol=2e-14)
            assert isclose(high, 1 - reflected_low, rel_tol=0, abs_tol=2e-14)
            assert cp_upper(leaks, scenarios, tail_probability=0.001) >= high
    assert all(cp_upper(0, n + 1) < cp_upper(0, n) for n in range(20, 100))


@pytest.mark.parametrize("scenarios", range(1, 21))
@pytest.mark.parametrize("confidence", [0.50, 0.90, 0.95, 0.99])
def test_equal_tailed_intervals_cover_at_least_nominal_binomial_probability(scenarios: int, confidence: float) -> None:
    intervals = [cp_interval(leaks, scenarios, confidence=confidence) for leaks in range(scenarios + 1)]
    for probability in (0.001, 0.01, 0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975, 0.99, 0.999):
        # Sum the sampling probability of every possible covered outcome.
        # The oracle uses elementary binomial arithmetic, not beta inversion.
        coverage = fsum(
            comb(scenarios, leaks) * probability**leaks * (1 - probability) ** (scenarios - leaks)
            for leaks, (lower, upper) in enumerate(intervals)
            if lower <= probability <= upper
        )
        assert coverage >= confidence - 2e-13


@pytest.mark.parametrize(
    ("leaks", "scenarios"),
    [(True, 20), (0, True), (0.0, 20), (0, 20.0), (-1, 20), (21, 20), (0, 0), (0, -1)],
)
def test_invalid_counts_are_rejected_by_both_public_functions(leaks, scenarios) -> None:
    with pytest.raises(ValueError):
        cp_upper(leaks, scenarios)
    with pytest.raises(ValueError):
        cp_interval(leaks, scenarios)


@pytest.mark.parametrize("value", [True, False, 0, 1, -0.1, 1.1, float("nan"), float("inf"), "0.95", 10**1000])
def test_invalid_tail_and_confidence_are_rejected(value) -> None:
    with pytest.raises(ValueError):
        cp_upper(0, 20, tail_probability=value)
    with pytest.raises(ValueError):
        cp_interval(0, 20, confidence=value)
