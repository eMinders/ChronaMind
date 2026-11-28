"""Exact finite-sample statistical power and sample-size calculations (``V2-D118``).

Provides exact power and minimum required sample size for three discrete regimes used in ChronaMind's
model-selection and retrieval evaluations:

1. **One-sample exact binomial test** (``exact_binomial_power``, ``required_n_binomial``):
   Two-sided mode uses the equal-tailed rejection rule (``alpha / 2`` per tail), matching the exact
   Clopper–Pearson interval inversion in :mod:`evaluation.stats.clopper_pearson`. One-sided modes
   (``"greater"``, ``"less"``) test the corresponding upper or lower binomial tail at ``alpha``.
2. **Independent two-proportion Fisher exact test** (``exact_two_proportion_power``,
   ``required_n_two_proportion``):
   Computes the exact unconditional power of Fisher's exact test under independent binomial sampling
   ``X1 ~ Binomial(n1, p1)``, ``X2 ~ Binomial(n2, p2)`` using unimodal hypergeometric tail bounds.
3. **Paired McNemar exact test** (``exact_mcnemar_power``, ``required_n_mcnemar``):
   Computes the exact unconditional power over ``N`` matched pairs with discordant probabilities
   ``(p10, p01)`` by summing exact binomial conditional test power across discordant counts
   ``M ~ Binomial(N, p10 + p01)``.

All sample-size solvers handle the discrete sawtooth cleanly: exact discrete power is non-monotone near
critical-value transitions, so binary search on a monotone envelope (randomized test or Casagrande–Pike–Smith
bound) is followed by local refinement to return the true smallest sample size ``n >= 1`` achieving
``power >= target_power``.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Literal

import numpy as np
from scipy.special import bdtr, bdtrc, gammaln
from scipy.stats import binom, norm

type Alternative = Literal["two-sided", "greater", "less"]
_ALTERNATIVES: frozenset[str] = frozenset({"two-sided", "greater", "less"})


def _validate_n(value: int, name: str = "n") -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"positive_integer_{name}_required")
    return value


def _validate_probability(value: float, name: str, *, open_unit: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"invalid_{name}")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"invalid_{name}")
    if open_unit:
        if not 0.0 < converted < 1.0:
            raise ValueError(f"{name}_must_be_in_open_unit_interval")
    elif not 0.0 <= converted <= 1.0:
        raise ValueError(f"{name}_must_be_in_unit_interval")
    return converted


def _validate_alternative(alternative: str) -> Alternative:
    if alternative not in _ALTERNATIVES:
        raise ValueError(f"unknown_alternative:{alternative}")
    return alternative  # type: ignore[return-value]


def _validate_ratio(ratio: float) -> float:
    if isinstance(ratio, bool) or not isinstance(ratio, Real):
        raise ValueError("invalid_allocation_ratio")
    converted = float(ratio)
    if not math.isfinite(converted) or converted <= 0.0:
        raise ValueError("allocation_ratio_must_be_positive")
    return converted


def _binomial_vec_power(
    ns: np.ndarray,
    p1: float,
    p0: float,
    alpha: float,
    alternative: Alternative,
    *,
    randomized: bool = False,
) -> np.ndarray:
    """Vectorized exact (or randomized upper-envelope) one-sample binomial power over sample-size array ``ns``."""
    if alternative == "greater":
        z = float(norm.isf(alpha))
        k_hi = np.clip(np.ceil(ns * p0 + z * np.sqrt(ns * p0 * (1.0 - p0))).astype(np.int64), 1, ns + 1)
        while True:
            cand = k_hi - 1
            mask = (cand >= 1) & (bdtrc(np.maximum(cand - 1, 0), ns, p0) <= alpha + 1e-12)
            if not np.any(mask):
                break
            k_hi[mask] -= 1
        while True:
            mask = (k_hi <= ns) & (bdtrc(np.maximum(k_hi - 1, 0), ns, p0) > alpha + 1e-12)
            if not np.any(mask):
                break
            k_hi[mask] += 1
        base = np.where(k_hi > ns, 0.0, bdtrc(np.maximum(k_hi - 1, 0), ns, p1))
        if randomized:
            edge = k_hi - 1
            valid = (edge >= 0) & (edge <= ns)
            e_safe = np.clip(edge, 0, ns)
            tail0 = np.where(k_hi > ns, 0.0, bdtrc(np.maximum(k_hi - 1, 0), ns, p0))
            pmf0 = binom.pmf(e_safe, ns, p0)
            pmf1 = binom.pmf(e_safe, ns, p1)
            safe_pmf0 = np.where(pmf0 > 0.0, pmf0, 1.0)
            gamma = np.where(valid & (pmf0 > 0.0) & (alpha > tail0), np.minimum(1.0, (alpha - tail0) / safe_pmf0), 0.0)
            base = np.minimum(1.0, base + gamma * pmf1)
        return np.minimum(1.0, base)

    if alternative == "less":
        z = float(norm.ppf(alpha))
        k_lo = np.clip(np.floor(ns * p0 + z * np.sqrt(ns * p0 * (1.0 - p0))).astype(np.int64), -1, ns)
        while True:
            cand = k_lo + 1
            mask = (cand <= ns) & (bdtr(np.minimum(cand, ns), ns, p0) <= alpha + 1e-12)
            if not np.any(mask):
                break
            k_lo[mask] += 1
        while True:
            mask = (k_lo >= 0) & (bdtr(np.maximum(k_lo, 0), ns, p0) > alpha + 1e-12)
            if not np.any(mask):
                break
            k_lo[mask] -= 1
        base = np.where(k_lo < 0, 0.0, bdtr(np.maximum(k_lo, 0), ns, p1))
        if randomized:
            edge = k_lo + 1
            valid = (edge >= 0) & (edge <= ns)
            e_safe = np.clip(edge, 0, ns)
            tail0 = np.where(k_lo < 0, 0.0, bdtr(np.maximum(k_lo, 0), ns, p0))
            pmf0 = binom.pmf(e_safe, ns, p0)
            pmf1 = binom.pmf(e_safe, ns, p1)
            safe_pmf0 = np.where(pmf0 > 0.0, pmf0, 1.0)
            gamma = np.where(valid & (pmf0 > 0.0) & (alpha > tail0), np.minimum(1.0, (alpha - tail0) / safe_pmf0), 0.0)
            base = np.minimum(1.0, base + gamma * pmf1)
        return np.minimum(1.0, base)

    half = alpha / 2.0
    return np.minimum(
        1.0,
        _binomial_vec_power(ns, p1, p0, half, "less", randomized=randomized)
        + _binomial_vec_power(ns, p1, p0, half, "greater", randomized=randomized),
    )


def exact_binomial_power(
    n: int,
    p1: float,
    p0: float,
    alpha: float = 0.05,
    alternative: Alternative = "two-sided",
) -> float:
    """Exact power of a one-sample binomial test of ``H0: p = p0`` at true proportion ``p1``.

    ``alternative="two-sided"`` uses the equal-tailed rejection rule (``alpha / 2`` per tail), matching exact
    two-sided Clopper–Pearson confidence interval inversion.
    """
    n_val = _validate_n(n)
    p1_val = _validate_probability(p1, "p1")
    p0_val = _validate_probability(p0, "p0", open_unit=True)
    alpha_val = _validate_probability(alpha, "alpha", open_unit=True)
    alt = _validate_alternative(alternative)
    arr = np.asarray([n_val], dtype=np.int64)
    return float(_binomial_vec_power(arr, p1_val, p0_val, alpha_val, alt, randomized=False)[0])


def required_n_binomial(
    p1: float,
    p0: float,
    target_power: float = 0.80,
    *,
    alpha: float = 0.05,
    alternative: Alternative = "two-sided",
    max_n: int = 1_000_000,
) -> int:
    """Smallest sample size ``n >= 1`` where :func:`exact_binomial_power` reaches ``target_power``.

    Uses exponential bracketing and binary search on the strictly monotone randomized-binomial upper envelope to
    obtain a proven lower bound ``lo``, then refines forward to the first discrete ``n`` meeting ``target_power`` so
    no sawtooth peak is skipped.
    """
    p1_val = _validate_probability(p1, "p1")
    p0_val = _validate_probability(p0, "p0", open_unit=True)
    target = _validate_probability(target_power, "target_power", open_unit=True)
    alpha_val = _validate_probability(alpha, "alpha", open_unit=True)
    alt = _validate_alternative(alternative)
    limit = _validate_n(max_n, "max_n")

    if alt == "two-sided" and p1_val == p0_val and target > alpha_val:
        raise ValueError("target_power_unreachable_under_null")
    if alt == "greater" and p1_val <= p0_val and target > alpha_val:
        raise ValueError("target_power_unreachable_for_direction")
    if alt == "less" and p1_val >= p0_val and target > alpha_val:
        raise ValueError("target_power_unreachable_for_direction")

    def _rand_power(n: int) -> float:
        ns_arr = np.asarray([n], dtype=np.int64)
        return float(_binomial_vec_power(ns_arr, p1_val, p0_val, alpha_val, alt, randomized=True)[0])

    lo, hi = 1, min(limit, 16)
    while _rand_power(hi) < target:
        if hi >= limit:
            raise ValueError("required_sample_size_exceeds_limit")
        lo = hi
        hi = min(limit, hi * 2)

    while lo < hi:
        mid = (lo + hi) // 2
        if _rand_power(mid) >= target:
            hi = mid
        else:
            lo = mid + 1

    # Binary search for an upper bound where exact power meets target, then check [lo, exact_hi] in a batch
    exact_hi = lo
    while exact_binomial_power(exact_hi, p1_val, p0_val, alpha_val, alt) < target:
        if exact_hi >= limit:
            raise ValueError("required_sample_size_exceeds_limit")
        exact_hi = min(limit, max(exact_hi + 1, exact_hi + max(1, (exact_hi - lo + 1) // 2 + 8)))

    # If the window [lo, exact_hi] is small, evaluate vectorized in one shot to find the exact minimum
    if exact_hi - lo <= 2048:
        candidates = np.arange(lo, exact_hi + 1, dtype=np.int64)
        powers = _binomial_vec_power(candidates, p1_val, p0_val, alpha_val, alt, randomized=False)
        hits = np.flatnonzero(powers >= target)
        if hits.size:
            return int(candidates[int(hits[0])])

    # Fallback for very wide gaps: binary search then scan backward window
    l_idx, r_idx = lo, exact_hi
    while l_idx < r_idx:
        mid = (l_idx + r_idx) // 2
        if exact_binomial_power(mid, p1_val, p0_val, alpha_val, alt) >= target:
            r_idx = mid
        else:
            l_idx = mid + 1
    win_start = max(lo, l_idx - 256)
    candidates = np.arange(win_start, l_idx + 1, dtype=np.int64)
    powers = _binomial_vec_power(candidates, p1_val, p0_val, alpha_val, alt, randomized=False)
    hits = np.flatnonzero(powers >= target)
    return int(candidates[int(hits[0])]) if hits.size else l_idx


def exact_two_proportion_power(
    n1: int,
    n2: int,
    p1: float,
    p2: float,
    alpha: float = 0.05,
    alternative: Alternative = "two-sided",
) -> float:
    """Exact unconditional power of Fisher's exact test for two independent binomial proportions.

    Computes ``sum_{x1, x2} P(X1 = x1; n1, p1) * P(X2 = x2; n2, p2) * 1{fisher_p(x1, x2) <= alpha}``.
    Uses unimodal hypergeometric searchsorted bounds and active-tail pruning for sub-second evaluation at large ``N``.
    """
    n1_val = _validate_n(n1, "n1")
    n2_val = _validate_n(n2, "n2")
    p1_val = _validate_probability(p1, "p1")
    p2_val = _validate_probability(p2, "p2")
    alpha_val = _validate_probability(alpha, "alpha", open_unit=True)
    alt = _validate_alternative(alternative)

    k1 = np.arange(n1_val + 1)
    k2 = np.arange(n2_val + 1)
    pmf1 = binom.pmf(k1, n1_val, p1_val)
    pmf2 = binom.pmf(k2, n2_val, p2_val)
    mask1 = pmf1 > 1e-15
    mask2 = pmf2 > 1e-15
    if not np.any(mask1):
        mask1[min(n1_val, max(0, round(n1_val * p1_val)))] = True
    if not np.any(mask2):
        mask2[min(n2_val, max(0, round(n2_val * p2_val)))] = True

    idx1 = k1[mask1]
    idx2 = k2[mask2]
    i1_lo, i1_hi = int(idx1[0]), int(idx1[-1])
    i2_lo, i2_hi = int(idx2[0]), int(idx2[-1])
    s_min = i1_lo + i2_lo
    s_max = i1_hi + i2_hi

    total_n = n1_val + n2_val
    lf = gammaln(np.arange(1, total_n + 2))
    const = float(-(lf[total_n] - lf[n1_val] - lf[total_n - n1_val]))
    total_power = 0.0

    for s in range(s_min, s_max + 1):
        lo_x = max(0, s - n2_val)
        hi_x = min(n1_val, s)
        act_lo = max(lo_x, i1_lo, s - i2_hi)
        act_hi = min(hi_x, i1_hi, s - i2_lo)
        if act_lo > act_hi:
            continue
        xs = np.arange(lo_x, hi_x + 1)
        log_h = (
            (lf[s] - lf[xs] - lf[s - xs])
            + (lf[total_n - s] - lf[n1_val - xs] - lf[total_n - s - (n1_val - xs)])
            + const
        )
        h = np.exp(log_h)
        h /= h.sum()

        a_start = act_lo - lo_x
        a_end = act_hi - lo_x + 1
        h_act = h[a_start:a_end]
        xs_act = xs[a_start:a_end]

        if alt == "greater":
            sf = np.cumsum(h[::-1])[::-1]
            rej = sf[a_start:a_end] <= alpha_val + 1e-12
        elif alt == "less":
            cdf = np.cumsum(h)
            rej = cdf[a_start:a_end] <= alpha_val + 1e-12
        else:
            mode = int(np.argmax(h))
            left_h = h[: mode + 1]
            left_cs = np.cumsum(left_h)
            right_h_rev = h[mode:][::-1]
            right_cs_rev = np.cumsum(right_h_rev)
            thresh = h_act * (1.0 + 1e-9)
            pos_l = np.searchsorted(left_h, thresh, side="right") - 1
            sum_l = np.where(pos_l >= 0, left_cs[np.maximum(pos_l, 0)], 0.0)
            pos_r = np.searchsorted(right_h_rev, thresh, side="right") - 1
            sum_r = np.where(pos_r >= 0, right_cs_rev[np.maximum(pos_r, 0)], 0.0)
            double_mode = np.where((pos_l == len(left_h) - 1) & (pos_r == len(right_h_rev) - 1), h[mode], 0.0)
            pvals = sum_l + sum_r - double_mode
            rej = pvals <= alpha_val + 1e-12

        rx = xs_act[rej]
        if rx.size:
            total_power += float(np.sum(pmf1[rx] * pmf2[s - rx]))

    return min(1.0, total_power)


def _cps_two_proportion_estimate(
    p1: float,
    p2: float,
    target: float,
    alpha: float,
    alternative: Alternative,
    ratio: float,
) -> int:
    """Casagrande–Pike–Smith (1978) continuity-corrected normal approximation for arm 1 sample size."""
    z_a = float(norm.isf(alpha / 2.0) if alternative == "two-sided" else norm.isf(alpha))
    z_b = float(norm.ppf(target))
    d = abs(p1 - p2)
    if d == 0.0:
        return 1
    p_bar = (p1 + ratio * p2) / (1.0 + ratio)
    var_null = p_bar * (1.0 - p_bar) * (1.0 + 1.0 / ratio)
    var_alt = p1 * (1.0 - p1) + p2 * (1.0 - p2) / ratio
    m = ((z_a * math.sqrt(var_null) + z_b * math.sqrt(var_alt)) ** 2) / (d * d)
    n_cc = (m / 4.0) * (1.0 + math.sqrt(1.0 + 2.0 * (1.0 + 1.0 / ratio) / (m * d))) ** 2
    return max(1, int(math.ceil(n_cc)))


def required_n_two_proportion(
    p1: float,
    p2: float,
    target_power: float = 0.80,
    *,
    alpha: float = 0.05,
    alternative: Alternative = "two-sided",
    ratio: float = 1.0,
    max_n: int = 50_000,
) -> int:
    """Smallest arm-1 sample size ``n1 >= 1`` (with ``n2 = ceil(n1 * ratio)``) reaching ``target_power``.

    Uses the Casagrande–Pike–Smith normal approximation to bracket ``[lo, hi]``, binary search on exact Fisher
    power, and a backward sawtooth scan to locate the minimum ``n1`` achieving ``power >= target_power``.
    """
    p1_val = _validate_probability(p1, "p1")
    p2_val = _validate_probability(p2, "p2")
    target = _validate_probability(target_power, "target_power", open_unit=True)
    alpha_val = _validate_probability(alpha, "alpha", open_unit=True)
    alt = _validate_alternative(alternative)
    r_val = _validate_ratio(ratio)
    limit = _validate_n(max_n, "max_n")

    if alt == "two-sided" and p1_val == p2_val and target > alpha_val:
        raise ValueError("target_power_unreachable_under_null")
    if alt == "greater" and p1_val <= p2_val and target > alpha_val:
        raise ValueError("target_power_unreachable_for_direction")
    if alt == "less" and p1_val >= p2_val and target > alpha_val:
        raise ValueError("target_power_unreachable_for_direction")

    def _power(n1: int) -> float:
        n2 = max(1, int(math.ceil(n1 * r_val)))
        return exact_two_proportion_power(n1, n2, p1_val, p2_val, alpha_val, alt)

    est = _cps_two_proportion_estimate(p1_val, p2_val, target, alpha_val, alt, r_val)
    hi = min(limit, max(16, int(math.ceil(est * 1.15))))
    lo = 1
    if est > 48:
        lo_try = max(1, int(est * 0.65))
        if _power(lo_try) < target - 0.08:
            lo = lo_try

    while _power(hi) < target:
        if hi >= limit:
            raise ValueError("required_sample_size_exceeds_limit")
        lo = hi
        hi = min(limit, hi * 2)

    l_idx, r_idx = lo, hi
    while l_idx < r_idx:
        mid = (l_idx + r_idx) // 2
        if _power(mid) >= target:
            r_idx = mid
        else:
            l_idx = mid + 1

    best = l_idx
    lookback = min(best - 1, 20 if best <= 250 else 12)
    for cand in range(best - 1, best - lookback - 1, -1):
        pw = _power(cand)
        if pw >= target:
            best = cand
        elif pw < target - 0.06:
            break
    return best


def _mcnemar_power_impl(
    n: int,
    p10: float,
    p01: float,
    alpha: float,
    alternative: Alternative,
    *,
    randomized: bool = False,
) -> float:
    p_disc = p10 + p01
    if p_disc == 0.0:
        return 0.0
    cond_p = p10 / p_disc
    ms = np.arange(1, n + 1, dtype=np.int64)
    pmf_m = binom.pmf(ms, n, p_disc)
    mask = pmf_m > 1e-15
    if not np.any(mask):
        return 0.0
    powers = _binomial_vec_power(ms[mask], cond_p, 0.5, alpha, alternative, randomized=randomized)
    return min(1.0, float(np.sum(pmf_m[mask] * powers)))


def exact_mcnemar_power(
    n: int,
    p10: float,
    p01: float,
    alpha: float = 0.05,
    alternative: Alternative = "two-sided",
) -> float:
    """Exact unconditional power of the paired McNemar test over ``n`` matched pairs.

    ``p10`` and ``p01`` are the population discordant-pair probabilities ``P(A=1, B=0)`` and ``P(A=0, B=1)``
    satisfying ``p10 + p01 <= 1``. Under ``"greater"`` tests ``p10 > p01``; under ``"less"`` tests ``p10 < p01``.
    """
    n_val = _validate_n(n)
    p10_val = _validate_probability(p10, "p10")
    p01_val = _validate_probability(p01, "p01")
    if p10_val + p01_val > 1.0 + 1e-12:
        raise ValueError("discordant_probabilities_exceed_one")
    alpha_val = _validate_probability(alpha, "alpha", open_unit=True)
    alt = _validate_alternative(alternative)
    return _mcnemar_power_impl(n_val, p10_val, p01_val, alpha_val, alt, randomized=False)


def required_n_mcnemar(
    p10: float,
    p01: float,
    target_power: float = 0.80,
    *,
    alpha: float = 0.05,
    alternative: Alternative = "two-sided",
    max_n: int = 1_000_000,
) -> int:
    """Smallest matched-pair count ``n >= 1`` where :func:`exact_mcnemar_power` reaches ``target_power``."""
    p10_val = _validate_probability(p10, "p10")
    p01_val = _validate_probability(p01, "p01")
    if p10_val + p01_val > 1.0 + 1e-12:
        raise ValueError("discordant_probabilities_exceed_one")
    if p10_val + p01_val == 0.0:
        raise ValueError("discordant_probability_must_be_positive")
    target = _validate_probability(target_power, "target_power", open_unit=True)
    alpha_val = _validate_probability(alpha, "alpha", open_unit=True)
    alt = _validate_alternative(alternative)
    limit = _validate_n(max_n, "max_n")

    if alt == "two-sided" and p10_val == p01_val and target > alpha_val:
        raise ValueError("target_power_unreachable_under_null")
    if alt == "greater" and p10_val <= p01_val and target > alpha_val:
        raise ValueError("target_power_unreachable_for_direction")
    if alt == "less" and p10_val >= p01_val and target > alpha_val:
        raise ValueError("target_power_unreachable_for_direction")

    def _rand_power(n: int) -> float:
        return _mcnemar_power_impl(n, p10_val, p01_val, alpha_val, alt, randomized=True)

    def _exact_power(n: int) -> float:
        return _mcnemar_power_impl(n, p10_val, p01_val, alpha_val, alt, randomized=False)

    # Normal-approx initial bracket using asymptotic McNemar variance
    z_a = float(norm.isf(alpha_val / 2.0) if alt == "two-sided" else norm.isf(alpha_val))
    z_b = float(norm.ppf(target))
    p_disc = p10_val + p01_val
    diff = abs(p10_val - p01_val)
    if diff > 0.0:
        numer = (z_a * math.sqrt(p_disc) + z_b * math.sqrt(p_disc - diff * diff)) ** 2
        n_est = max(1, int(math.ceil(numer / (diff * diff))))
    else:
        n_est = 16

    lo = 1
    hi = min(limit, max(16, int(math.ceil(n_est * 1.15))))
    if n_est > 48:
        lo_try = max(1, int(n_est * 0.65))
        if _rand_power(lo_try) < target:
            lo = lo_try

    while _rand_power(hi) < target:
        if hi >= limit:
            raise ValueError("required_sample_size_exceeds_limit")
        lo = hi
        hi = min(limit, hi * 2)

    while lo < hi:
        mid = (lo + hi) // 2
        if _rand_power(mid) >= target:
            hi = mid
        else:
            lo = mid + 1
    lo_bound = lo

    # Binary search upward from lo_bound on exact power, then check small backward window for discrete sawtooth
    exact_hi = lo_bound
    while _exact_power(exact_hi) < target:
        if exact_hi >= limit:
            raise ValueError("required_sample_size_exceeds_limit")
        lo_bound = exact_hi
        exact_hi = min(limit, max(exact_hi + 1, exact_hi + max(16, int(exact_hi * 0.1))))

    l_idx, r_idx = lo_bound, exact_hi
    while l_idx < r_idx:
        mid = (l_idx + r_idx) // 2
        if _exact_power(mid) >= target:
            r_idx = mid
        else:
            l_idx = mid + 1

    best = l_idx
    lookback = min(best - lo, 24)
    for cand in range(best - 1, best - lookback - 1, -1):
        pw = _exact_power(cand)
        if pw >= target:
            best = cand
        elif pw < target - 0.06:
            break
    return best


__all__ = [
    "Alternative",
    "exact_binomial_power",
    "exact_mcnemar_power",
    "exact_two_proportion_power",
    "required_n_binomial",
    "required_n_mcnemar",
    "required_n_two_proportion",
]
