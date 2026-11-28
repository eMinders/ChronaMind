"""Holm step-down adjustment and the predeclared three-hypothesis family."""

from collections.abc import Mapping
from math import isfinite


def _probability(value: float) -> float:
    """Reject coercions and booleans as well as out-of-range/nonfinite values."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise ValueError("finite_probability_in_unit_interval_required")
    if not isfinite(value):
        raise ValueError("finite_probability_in_unit_interval_required")
    return float(value)


def holm_adjust(p_values: Mapping[str, float], *, alpha: float = 0.05) -> dict[str, float]:
    """Return Holm-adjusted p-values in the input mapping's order, without mutation.

    Adjusted p-values do not depend on the chosen significance level. ``alpha``
    is validated as a finite level strictly between zero and one; consumers
    compare the returned values with it. The confirmatory contract uses the
    strict criterion adjusted p < .05, separately from this calculation.
    """
    level = _probability(alpha)
    if not 0 < level < 1:
        raise ValueError("significance_level_strictly_between_zero_and_one_required")
    if not p_values:
        raise ValueError("nonempty_hypothesis_family_required")

    validated = {name: _probability(value) for name, value in p_values.items()}
    sorted_values = sorted(validated.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    previous = 0.0
    family_size = len(sorted_values)
    for rank, (name, value) in enumerate(sorted_values):
        previous = min(1.0, max(previous, (family_size - rank) * value))
        adjusted[name] = previous
    return {name: adjusted[name] for name in validated}


def confirmatory_holm(*, h1_history: float, h2_dense: float, h2_lexical: float, h3_asr: float) -> dict[str, float]:
    """Adjust exactly H1 history, H2's max-p IUT, and H3 ASR at global alpha .05.

    Proposal §10.6 defines H2 as one family member, the larger of its two
    unadjusted contrast p-values. Non-inferiority bounds are separate IUT
    conditions, so neither they nor H4 add members to this family.
    """
    # Validate both H2 inputs before max: nan or a negative value must never be
    # hidden by an otherwise valid, larger p-value from the other contrast.
    dense = _probability(h2_dense)
    lexical = _probability(h2_lexical)
    return holm_adjust(
        {
            "H1": _probability(h1_history),
            "H2": max(dense, lexical),
            "H3": _probability(h3_asr),
        },
        alpha=0.05,
    )
