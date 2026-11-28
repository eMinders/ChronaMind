"""Block/session/source dependence adjustment and effective sample size ``n_eff`` (SD-008).

Implements the three conservative dependence bounds required by ``PLAN/V2/STATISTICS.md`` §5.4.2 and
``PLAN/V2/ROADMAP_V2.md`` §7 (``SD-008``):

1. **Cluster-level Clopper–Pearson bound**: collapses each independent block/session/source cluster into one
   worst-case unit (any counter or undetermined item in the cluster marks the cluster as failed).
2. **Design-effect adjusted bound**: estimates intra-class correlation ``rho`` via one-way random-effects ANOVA and
   its conservative upper confidence limit ``rho_upper``, computes Kish's design effect
   ``deff = 1 + (m_eff - 1) * rho_upper``, and bounds effective sample size ``n_eff = floor(o / deff) <= o``.
3. **Block bootstrap bound**: resamples whole clusters with replacement (``B >= 10_000``) to obtain the 97.5th
   percentile upper limit on the failure proportion.

Takes the **most conservative** (maximum failure upper bound / minimum confidence lower bound) across the bounds.
Never inflates ``n_eff`` above raw ``o``, and never claims "exact" under clustering (``STATISTICS.md`` §5.4.2).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from math import ceil, floor, isfinite
from typing import Literal

import numpy as np
from pydantic import Field
from scipy.stats import f as f_dist

from evaluation.stats.abstraction_metrics import TAIL_PROBABILITY
from evaluation.stats.clopper_pearson import cp_upper
from evaluation.stats.models import StatsRecord

type OutcomeState = Literal["support", "counter", "undetermined"]


class ClusterObservation(StatsRecord):
    """One evidence opportunity tagged with its dependence cluster (session/turn/episode/time-block)."""

    cluster_id: str = Field(min_length=1)
    outcome: OutcomeState


class DependenceReport(StatsRecord):
    """Conservative dependence-adjusted uncertainty report (never labelled "exact")."""

    raw_opportunity: int = Field(ge=1)
    raw_failures: int = Field(ge=0)
    n_clusters: int = Field(ge=1)
    mean_cluster_size: float = Field(ge=1.0)
    kish_cluster_size: float = Field(ge=1.0)
    estimated_rho: float = Field(ge=0.0, le=1.0)
    conservative_rho: float = Field(ge=0.0, le=1.0)
    design_effect: float = Field(ge=1.0)
    effective_sample_size: int = Field(ge=1)
    naive_cp_upper: float = Field(ge=0.0, le=1.0)
    cluster_cp_upper: float = Field(ge=0.0, le=1.0)
    design_effect_cp_upper: float = Field(ge=0.0, le=1.0)
    block_bootstrap_upper: float = Field(ge=0.0, le=1.0)
    conservative_failure_upper: float = Field(ge=0.0, le=1.0)
    conservative_confidence_lower: float = Field(ge=0.0, le=1.0)
    guarantee_label: Literal["approximately_valid_conservative_under_rho_upper"] = (
        "approximately_valid_conservative_under_rho_upper"
    )


def design_effect_sample_size(opportunity: int, block_size: float, rho: float) -> int:
    """Conservative integer effective sample size ``1 <= n_eff <= opportunity`` from ``deff = 1 + (m - 1) * rho``."""
    if isinstance(opportunity, bool) or not isinstance(opportunity, int) or opportunity < 1:
        raise ValueError("positive_integer_opportunity_required")
    if isinstance(block_size, bool) or not isinstance(block_size, int | float):
        raise ValueError("invalid_block_size")
    m = float(block_size)
    if not isfinite(m) or m < 1.0:
        raise ValueError("block_size_must_be_at_least_one")
    if isinstance(rho, bool) or not isinstance(rho, int | float):
        raise ValueError("invalid_rho")
    icc = float(rho)
    if not isfinite(icc) or not 0.0 <= icc <= 1.0:
        raise ValueError("rho_must_be_in_unit_interval")
    deff = 1.0 + (m - 1.0) * icc
    n_eff = floor(opportunity / deff)
    return max(1, min(opportunity, n_eff))


def _kish_cluster_size(sizes: Sequence[int]) -> float:
    total = sum(sizes)
    return sum(size * size for size in sizes) / total


def estimate_intraclass_correlation(
    clusters: Sequence[Sequence[int]],
    *,
    confidence_tail: float = TAIL_PROBABILITY,
) -> tuple[float, float]:
    """Estimate ``(rho_hat, rho_conservative_upper)`` via one-way ANOVA on binary failure indicators.

    Conservative fallback rules (``STATISTICS.md`` §5.4.2):
    * When every cluster has size 1 (``G == N``), items are unclustered: ``(0.0, 0.0)``.
    * When ``G == 1`` with ``N > 1``, between-cluster variance is unidentifiable: ``(1.0, 1.0)``.
    * When all outcomes are constant (``MSW == 0`` and ``MSB == 0``) and ``max(m_c) > 1``, zero observed
      variance gives zero evidence against clustering, so ``rho_upper = 1.0`` conservatively collapses
      effective sample size toward ``G``.
    """
    if not clusters or any(not c for c in clusters):
        raise ValueError("nonempty_clusters_required")
    if isinstance(confidence_tail, bool) or not isinstance(confidence_tail, int | float):
        raise ValueError("tail_probability_out_of_range")
    tail = float(confidence_tail)
    if not isfinite(tail) or not 0.0 < tail < 0.5:
        raise ValueError("tail_probability_out_of_range")

    sizes = [len(c) for c in clusters]
    total = sum(sizes)
    n_clusters = len(clusters)
    if max(sizes) <= 1:
        return (0.0, 0.0)
    if n_clusters == 1:
        return (1.0, 1.0)

    for cluster in clusters:
        for val in cluster:
            if isinstance(val, bool) or not isinstance(val, int) or val not in (0, 1):
                raise ValueError("cluster_values_must_be_binary_integers")

    grand_sum = sum(sum(c) for c in clusters)
    grand_mean = grand_sum / total
    ss_between = sum(len(c) * ((sum(c) / len(c)) - grand_mean) ** 2 for c in clusters)
    ss_within = sum(sum((y - (sum(c) / len(c))) ** 2 for y in c) for c in clusters)

    df_between = n_clusters - 1
    df_within = total - n_clusters
    msb = ss_between / df_between
    msw = ss_within / df_within
    m_0 = (total - sum(m * m for m in sizes) / total) / df_between

    if msb == 0.0 and msw == 0.0:
        return (0.0, 1.0)
    if msw == 0.0:
        return (1.0, 1.0)

    denom = msb + (m_0 - 1.0) * msw
    rho_hat = 0.0 if denom <= 0.0 else max(0.0, min(1.0, (msb - msw) / denom))

    f_obs = msb / msw
    f_lower_crit = float(f_dist.ppf(tail, df_between, df_within))
    if not isfinite(f_lower_crit) or f_lower_crit <= 0.0:
        rho_upper = 1.0
    else:
        theta_upper = max(0.0, (f_obs / f_lower_crit - 1.0) / m_0)
        rho_upper = min(1.0, theta_upper / (1.0 + theta_upper))
    return (rho_hat, max(rho_hat, rho_upper))


def block_bootstrap_failure_upper(
    clusters: Sequence[Sequence[int]],
    *,
    seed: int = 0,
    resamples: int = 10_000,
    quantile: float = 0.975,
) -> float:
    """Block bootstrap upper percentile over whole clusters (``STATISTICS.md`` §5.4.2)."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("nonnegative_integer_seed_required")
    if isinstance(resamples, bool) or not isinstance(resamples, int) or resamples < 10_000:
        raise ValueError("at_least_10000_bootstrap_resamples_required")
    if isinstance(quantile, bool) or not isinstance(quantile, int | float):
        raise ValueError("quantile_out_of_range")
    q = float(quantile)
    if not isfinite(q) or not 0.0 < q < 1.0:
        raise ValueError("quantile_out_of_range")
    if not clusters or any(not c for c in clusters):
        raise ValueError("nonempty_clusters_required")

    cluster_failures = np.asarray([sum(c) for c in clusters], dtype=np.int64)
    cluster_sizes = np.asarray([len(c) for c in clusters], dtype=np.int64)
    n_clusters = len(clusters)
    generator = np.random.Generator(np.random.PCG64(seed))
    replicates = np.empty(resamples, dtype=np.float64)
    batch = max(1, min(512, 262_144 // n_clusters))
    for start in range(0, resamples, batch):
        size = min(batch, resamples - start)
        indices = generator.integers(0, n_clusters, size=(size, n_clusters))
        fail_sums = cluster_failures[indices].sum(axis=1)
        size_sums = cluster_sizes[indices].sum(axis=1)
        replicates[start : start + size] = fail_sums / size_sums
    return float(min(1.0, max(0.0, np.quantile(replicates, q, method="linear"))))


def assess_dependence(
    observations: Sequence[ClusterObservation],
    *,
    seed: int = 0,
    resamples: int = 10_000,
    rho_override: float | None = None,
    tail_probability: float = TAIL_PROBABILITY,
) -> DependenceReport:
    """Compute all three dependence-adjusted bounds and return the most conservative upper limit."""
    if not observations:
        raise ValueError("nonempty_observations_required")
    if isinstance(tail_probability, bool) or not isinstance(tail_probability, int | float):
        raise ValueError("tail_probability_out_of_range")
    tail = float(tail_probability)
    if not isfinite(tail) or not 0.0 < tail < 0.5:
        raise ValueError("tail_probability_out_of_range")

    grouped: dict[str, list[int]] = defaultdict(list)
    for obs in observations:
        if not isinstance(obs, ClusterObservation):
            raise ValueError("cluster_observation_required")
        # Under partial identification, both counter and undetermined count as failures in the upper bound.
        grouped[obs.cluster_id].append(0 if obs.outcome == "support" else 1)

    clusters = [grouped[cid] for cid in sorted(grouped)]
    sizes = [len(c) for c in clusters]
    raw_o = len(observations)
    raw_failures = sum(sum(c) for c in clusters)
    n_clusters = len(clusters)
    mean_m = raw_o / n_clusters
    kish_m = _kish_cluster_size(sizes)

    rho_hat, rho_upper = estimate_intraclass_correlation(clusters, confidence_tail=tail)
    if rho_override is not None:
        if isinstance(rho_override, bool) or not isinstance(rho_override, int | float):
            raise ValueError("invalid_rho")
        override = float(rho_override)
        if not isfinite(override) or not 0.0 <= override <= 1.0:
            raise ValueError("rho_must_be_in_unit_interval")
        conservative_rho = max(rho_upper, override)
    else:
        conservative_rho = rho_upper

    deff = 1.0 + (kish_m - 1.0) * conservative_rho
    n_eff = design_effect_sample_size(raw_o, kish_m, conservative_rho)
    eff_failures = min(n_eff, ceil(raw_failures * (n_eff / raw_o)))

    naive_upper = cp_upper(raw_failures, raw_o, tail_probability=tail)
    cluster_failures = sum(1 for c in clusters if any(val == 1 for val in c))
    cluster_upper = cp_upper(cluster_failures, n_clusters, tail_probability=tail)
    deff_upper = cp_upper(eff_failures, n_eff, tail_probability=tail)
    boot_upper = block_bootstrap_failure_upper(
        clusters,
        seed=seed,
        resamples=resamples,
        quantile=1.0 - tail,
    )
    conservative_upper = max(naive_upper, cluster_upper, deff_upper, boot_upper)

    return DependenceReport(
        raw_opportunity=raw_o,
        raw_failures=raw_failures,
        n_clusters=n_clusters,
        mean_cluster_size=mean_m,
        kish_cluster_size=kish_m,
        estimated_rho=rho_hat,
        conservative_rho=conservative_rho,
        design_effect=deff,
        effective_sample_size=n_eff,
        naive_cp_upper=naive_upper,
        cluster_cp_upper=cluster_upper,
        design_effect_cp_upper=deff_upper,
        block_bootstrap_upper=boot_upper,
        conservative_failure_upper=conservative_upper,
        conservative_confidence_lower=max(0.0, 1.0 - conservative_upper),
    )


__all__ = [
    "ClusterObservation",
    "DependenceReport",
    "OutcomeState",
    "assess_dependence",
    "block_bootstrap_failure_upper",
    "design_effect_sample_size",
    "estimate_intraclass_correlation",
]
