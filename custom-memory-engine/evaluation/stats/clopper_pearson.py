"""Exact binomial bounds for independent scenarios, not within-scenario events."""

from math import expm1, isfinite, log

from scipy.special import betainccinv, betaincinv


def _counts(leaks: int, scenarios: int) -> None:
    if type(leaks) is not int or type(scenarios) is not int:
        raise ValueError("integer_scenario_counts_required")
    if scenarios < 1 or not 0 <= leaks <= scenarios:
        raise ValueError("invalid_scenario_counts")


def _probability(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < 1 or not isfinite(value):
        raise ValueError(f"invalid_{name}")


def cp_upper(leaks: int, scenarios: int, *, tail_probability: float = 0.025) -> float:
    """One-sided upper bound with coverage ``1 - tail_probability``.

    The default is the upper end of a two-sided 95% Clopper–Pearson interval,
    equivalently a one-sided 97.5% upper bound. Complementary-beta inversion
    avoids rounding ``1 - tail_probability`` to one for very small tails.
    """
    _counts(leaks, scenarios)
    _probability(tail_probability, "tail_probability")
    if leaks == scenarios:
        return 1.0
    if leaks == 0:
        return -expm1(log(tail_probability) / scenarios)
    result = float(betainccinv(leaks + 1, scenarios - leaks, tail_probability))
    if not isfinite(result) or not 0 <= result <= 1:
        raise ValueError("binomial_bound_numerical_failure")
    return result


def cp_interval(leaks: int, scenarios: int, *, confidence: float = 0.95) -> tuple[float, float]:
    """Equal-tailed, two-sided Clopper–Pearson interval for scenario counts."""
    _counts(leaks, scenarios)
    _probability(confidence, "confidence")
    tail = (1 - confidence) / 2
    lower = 0.0 if leaks == 0 else float(betaincinv(leaks, scenarios - leaks + 1, tail))
    upper = cp_upper(leaks, scenarios, tail_probability=tail)
    if not isfinite(lower) or not 0 <= lower <= upper:
        raise ValueError("binomial_bound_numerical_failure")
    return lower, upper
