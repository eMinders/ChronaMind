"""Two-sided sign-flip inference on independent, already aggregated clusters.

The statistic is the mean paired difference. Flipping the sum gives the same
ordering because its denominator is fixed. Exactly zero differences remain in
the reported estimate/counts but do not multiply the permutation space.
"""

from collections.abc import Sequence
from hashlib import sha256
from json import dumps
from math import fsum, isfinite
from numbers import Real
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.stats.arithmetic import exact_mean


class SignFlipResult(BaseModel):
    """A reproducible test result; no conclusion or research approval is implied."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)
    estimate: float = Field(ge=-1, le=1)
    p_value: float = Field(ge=0, le=1)
    method: Literal["exact", "monte_carlo"]
    seed: int = Field(ge=0)
    n_clusters: int = Field(ge=1)
    n_nonzero: int = Field(ge=0)
    n_zero: int = Field(ge=0)
    n_positive: int = Field(ge=0)
    n_negative: int = Field(ge=0)
    permutations: int = Field(ge=1)
    extreme_count: int = Field(ge=0)
    differences_sha256: str = Field(pattern="^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def consistent_counts(self) -> "SignFlipResult":
        if self.n_positive + self.n_negative != self.n_nonzero:
            raise ValueError("inconsistent_nonzero_cluster_count")
        if self.n_nonzero + self.n_zero != self.n_clusters:
            raise ValueError("inconsistent_total_cluster_count")
        if self.extreme_count > self.permutations:
            raise ValueError("extreme_count_exceeds_permutations")
        expected = (
            self.extreme_count / self.permutations
            if self.method == "exact"
            else (self.extreme_count + 1) / (self.permutations + 1)
        )
        if self.p_value != expected:
            raise ValueError("p_value_count_mismatch")
        if self.method == "exact" and self.permutations != 2**self.n_nonzero:
            raise ValueError("incomplete_exact_permutation_space")
        return self


def _validated_differences(differences: Sequence[float]) -> tuple[float, ...]:
    if len(differences) == 0:
        raise ValueError("nonempty_cluster_differences_required")
    values: list[float] = []
    for value in differences:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("finite_real_cluster_differences_required")
        if not -1 <= value <= 1:
            raise ValueError("cluster_difference_outside_unit_interval")
        converted = float(value)
        if not isfinite(converted):
            raise ValueError("finite_real_cluster_differences_required")
        values.append(converted)
    return tuple(values)


def differences_digest(differences: Sequence[float]) -> str:
    """Bind a test to its ordered finite cluster effects, preserving exact floats."""
    values = _validated_differences(differences)
    return sha256(dumps(values, separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()


def _integer_weights(values: tuple[float, ...]) -> tuple[int, ...]:
    """Put binary floats on one integer lattice for exact boundary comparisons."""
    ratios = tuple(value.as_integer_ratio() for value in values)
    denominator = max((ratio[1] for ratio in ratios), default=1)
    return tuple(numerator * (denominator // divisor) for numerator, divisor in ratios)


def _count_extreme(
    signs: NDArray[np.int64],
    values: NDArray[np.float64],
    integer_weights: tuple[int, ...],
    observed_integer: int,
    observed_float: float,
    roundoff_bound: float,
) -> int:
    """Vectorize clear cases; decide near-threshold cases on the exact lattice.

    The floating error bound only selects cases needing exact evaluation. It
    never changes whether a genuinely distinct statistic counts as a tie.
    """
    if observed_integer == 0:
        return len(signs)
    magnitudes = np.abs(np.sum(signs * values, axis=1))
    lower = max(0.0, observed_float - roundoff_bound)
    upper = observed_float + roundoff_bound
    count = int(np.count_nonzero(magnitudes > upper))
    near_indices = np.flatnonzero((magnitudes >= lower) & (magnitudes <= upper))
    for index in near_indices:
        signed_integer = sum(int(sign) * weight for sign, weight in zip(signs[index], integer_weights, strict=True))
        count += abs(signed_integer) >= observed_integer
    return count


def paired_permutation(
    differences: Sequence[float],
    *,
    seed: int,
    resamples: int = 100_000,
    max_exact_clusters: int = 16,
) -> SignFlipResult:
    """Test paired cluster differences with a two-sided absolute-mean statistic.

    Enumerate all signs of nonzero clusters when their count is at most the
    explicit exact limit (0..20). Otherwise draw independent signs using a local
    PCG64 generator and return ``(extreme_count + 1) / (resamples + 1)``. An exact
    test returns the unadjusted enumerated fraction. Every seed is explicit and
    recorded, including for exact tests. Inputs must already average repeated
    runs within items and items within independent clusters (Proposal §10.6).
    The null requires exchangeability of each cluster's sign, not normality.
    """
    if type(seed) is not int or seed < 0:
        raise ValueError("nonnegative_integer_seed_required")
    if type(resamples) is not int or resamples < 1:
        raise ValueError("positive_integer_resamples_required")
    if type(max_exact_clusters) is not int or not 0 <= max_exact_clusters <= 20:
        raise ValueError("exact_cluster_limit_must_be_between_zero_and_twenty")
    original = _validated_differences(differences)
    nonzero = tuple(value for value in original if value != 0)
    n_nonzero = len(nonzero)
    method: Literal["exact", "monte_carlo"] = "exact" if n_nonzero <= max_exact_clusters else "monte_carlo"
    permutations = 2**n_nonzero if method == "exact" else resamples
    weights = np.asarray(nonzero, dtype=np.float64)
    integer_weights = _integer_weights(nonzero)
    observed_integer = abs(sum(integer_weights))
    observed_float = abs(fsum(nonzero))
    roundoff_bound = fsum(abs(value) for value in nonzero) * np.finfo(np.float64).eps * max(1, n_nonzero) * 4
    # Both algorithms use bounded batches, rather than allocating B x N arrays.
    batch_size = max(1, min(256, 262_144 // max(1, n_nonzero)))
    rng = np.random.Generator(np.random.PCG64(seed))
    bit_positions = np.arange(n_nonzero, dtype=np.uint64) if method == "exact" else None
    extreme_count = 0
    for start in range(0, permutations, batch_size):
        batch = min(batch_size, permutations - start)
        if bit_positions is not None:
            indices = np.arange(start, start + batch, dtype=np.uint64)
            bits = ((indices[:, None] >> bit_positions) & 1).astype(np.int64)
        else:
            bits = rng.integers(0, 2, size=(batch, n_nonzero))
        signs = bits * 2 - 1
        extreme_count += _count_extreme(
            signs, weights, integer_weights, observed_integer, observed_float, float(roundoff_bound)
        )
    p_value = extreme_count / permutations if method == "exact" else (extreme_count + 1) / (permutations + 1)
    return SignFlipResult(
        estimate=exact_mean(original),
        p_value=p_value,
        method=method,
        seed=seed,
        n_clusters=len(original),
        n_nonzero=n_nonzero,
        n_zero=len(original) - n_nonzero,
        n_positive=sum(value > 0 for value in original),
        n_negative=sum(value < 0 for value in original),
        permutations=permutations,
        extreme_count=extreme_count,
        differences_sha256=differences_digest(original),
    )
