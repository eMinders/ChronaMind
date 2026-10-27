"""Proposal §10.6 percentile bootstrap of whole paired independent clusters."""

from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from evaluation.stats.arithmetic import exact_mean, integer_lattice
from evaluation.stats.models import ClusterEffect, ConfidenceInterval, PairedSample, StatsRecord


class BootstrapResult(StatsRecord):
    """Effect size and uncertainty; cluster effects also supply the required plot data."""

    estimate: float = Field(ge=-1, le=1)
    ci95: ConfidenceInterval
    conservative_ci: ConfidenceInterval
    noninferiority_lower: float = Field(ge=-1, le=1)
    clusters: tuple[ClusterEffect, ...] = Field(min_length=1)
    seed: int = Field(ge=0)
    resamples: int = Field(ge=10_000)
    rng: Literal["numpy.random.PCG64"] = "numpy.random.PCG64"
    percentile_method: Literal["linear"] = "linear"

    @model_validator(mode="after")
    def consistent_summary(self) -> "BootstrapResult":
        if len({cluster.cluster_id for cluster in self.clusters}) != len(self.clusters):
            raise ValueError("duplicate_cluster_effect")
        if self.estimate != exact_mean([cluster.difference for cluster in self.clusters]):
            raise ValueError("estimate_cluster_effect_mismatch")
        if self.ci95.level != 0.95 or self.conservative_ci.level != 1 - 0.05 / 3:
            raise ValueError("incorrect_predeclared_interval_level")
        if self.conservative_ci.low > self.ci95.low or self.conservative_ci.high < self.ci95.high:
            raise ValueError("conservative_interval_must_enclose_95_interval")
        if self.noninferiority_lower != self.ci95.low:
            raise ValueError("noninferiority_lower_must_equal_95_lower")
        return self


def cluster_bootstrap(sample: PairedSample, *, seed: int, resamples: int = 10_000) -> BootstrapResult:
    """Average runs/items first; resample cluster IDs with their complete paired data.

    Percentile endpoints use NumPy's explicit linear interpolation. The 2.5th
    percentile is both the two-sided 95% lower endpoint and the one-sided 97.5%
    non-inferiority lower bound. No margin or research approval is inferred here.
    """
    if type(seed) is not int or seed < 0:
        raise ValueError("nonnegative_integer_seed_required")
    if type(resamples) is not int or resamples < 10_000:
        raise ValueError("at_least_10000_bootstrap_resamples_required")
    clusters = sample.cluster_effects()
    effects = [cluster.difference for cluster in clusters]
    weights, denominator = integer_lattice(effects)
    # Object arrays preserve Python integers even for subnormal inputs. Exact
    # sums prevent constant samples from moving across strict NI boundaries.
    values = np.asarray(weights, dtype=object)
    divisor = denominator * len(clusters)
    generator = np.random.Generator(np.random.PCG64(seed))
    replicates = np.empty(resamples, dtype=np.float64)
    # Limit temporary index/value matrices independently of the number of replicates.
    batch = max(1, min(256, 262_144 // len(clusters)))
    for start in range(0, resamples, batch):
        size = min(batch, resamples - start)
        indices = generator.integers(0, len(clusters), size=(size, len(clusters)))
        sums = values[indices].sum(axis=1)
        replicates[start : start + size] = np.fromiter((int(total) / divisor for total in sums), dtype=np.float64)
    lower, upper, conservative_lower, conservative_upper = np.quantile(
        replicates, [0.025, 0.975, 0.05 / 6, 1 - 0.05 / 6], method="linear"
    )
    return BootstrapResult(
        estimate=exact_mean(effects),
        ci95=ConfidenceInterval(level=0.95, low=float(lower), high=float(upper)),
        conservative_ci=ConfidenceInterval(
            level=1 - 0.05 / 3, low=float(conservative_lower), high=float(conservative_upper)
        ),
        noninferiority_lower=float(lower),
        clusters=clusters,
        seed=seed,
        resamples=resamples,
    )
