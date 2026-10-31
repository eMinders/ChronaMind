"""Where the Clopper–Pearson bound applies: verification, not creation (V2-D40, resolves B-06).

``ARCHITECTURE_V2.md`` §2.2 drew G2 failing to "rejected", while ``STATISTICS.md`` §4.2
renders a below-τ abstraction as "hypothesis only" — i.e. it exists. ``STATISTICS.md``
§4.1 point 3 settles it: verification "requires the abstraction to be admitted first
(you must store it to test it against held-out data)". The decision:

* **Creation** (``infer``) is gated only by the *structural* soundness checks (declared
  method, readable sources, well-formed ledger, …); a candidate that passes them is
  stored as ``inferred`` with ``verified=false`` and renders in ``hypotheses`` — never in
  ``facts``. G2's "rejected" branch applies to those structural failures.
* **Verification** (``inferred → inferred(verified=true)``, §2.3) requires
  ``admissible`` per ``STATISTICS.md`` §4.2 **and** a passed held-out verification
  (``V2-D21``). Evidence can accumulate on a stored hypothesis until it qualifies.

Nothing is weakened: the convention stays one-sided 97.5%, undetermined observations
count as failures in the upper bound (``confidence_lower = 1 − CP_upper(k + u, o)``,
the r3 correction), coverage stays a gate, and the thresholds default to the documented
values. The bound is exact (beta quantile), matching ``evaluation.stats.clopper_pearson``
and ``validation.cp_bounds`` within 1e-12 (tested); the engine does not import either.
"""

from __future__ import annotations

from dataclasses import dataclass

from scipy.stats import beta

from custom_memory_engine.model.axes import require_transition

TAIL_PROBABILITY = 0.025  # one-sided 97.5% (V2-D18/D19, STATISTICS.md §4.2)
DEFAULT_TAU_CONFIDENCE = 0.90
DEFAULT_TAU_COVERAGE = 0.60
MIN_REFUTATION_ATTEMPTS = 3


class AdmissionError(ValueError):
    """Static code; never echoes ledger content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _count(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AdmissionError(code)
    return value


def cp_upper(failures: int, trials: int, *, tail_probability: float = TAIL_PROBABILITY) -> float:
    """Exact one-sided Clopper–Pearson upper limit on a failure proportion."""
    failures = _count(failures, "failures_must_be_a_nonnegative_integer")
    trials = _count(trials, "trials_must_be_a_nonnegative_integer")
    if trials == 0 or failures > trials:
        raise AdmissionError("failures_must_not_exceed_positive_trials")
    if not 0 < tail_probability < 1:
        raise AdmissionError("tail_probability_out_of_range")
    if failures == trials:
        return 1.0
    return float(beta.ppf(1 - tail_probability, failures + 1, trials - failures))


@dataclass(frozen=True, slots=True)
class Ledger:
    """Three-state evidence ledger (V2-D18): ``o = s + k + u``."""

    support: int
    counter: int
    undetermined: int

    def __post_init__(self) -> None:
        for value, code in (
            (self.support, "support_count_invalid"),
            (self.counter, "counter_count_invalid"),
            (self.undetermined, "undetermined_count_invalid"),
        ):
            _count(value, code)
        if self.opportunity == 0:
            raise AdmissionError("opportunity_count_must_be_positive")

    @property
    def opportunity(self) -> int:
        return self.support + self.counter + self.undetermined


@dataclass(frozen=True, slots=True)
class Assessment:
    confidence_lower: float
    coverage: float
    failure_upper: float
    admissible: bool
    reasons: tuple[str, ...]

    @property
    def rendering(self) -> str:
        """``STATISTICS.md`` §4.2 rendering column."""
        if not self.admissible:
            return "hypothesis only"
        return f"≤ {self.failure_upper * 100:.1f}% failure rate at 97.5% confidence"


def assess(
    ledger: Ledger,
    *,
    refutation_attempts: int,
    negative_control_clean: bool,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
) -> Assessment:
    """Apply the §4.2 admission rule. Every failing clause is reported, not just the first."""
    attempts = _count(refutation_attempts, "refutation_attempts_invalid")
    if not isinstance(negative_control_clean, bool):
        raise AdmissionError("negative_control_flag_must_be_boolean")
    for tau in (tau_confidence, tau_coverage):
        if not isinstance(tau, float) or not 0 < tau < 1:
            raise AdmissionError("threshold_out_of_range")
    o = ledger.opportunity
    upper = cp_upper(ledger.counter + ledger.undetermined, o)  # u counted as failure (r3)
    confidence_lower = 1.0 - upper
    coverage = (ledger.support + ledger.counter) / o
    reasons: list[str] = []
    if confidence_lower < tau_confidence:
        reasons.append("confidence_lower_below_threshold")
    if coverage < tau_coverage:
        reasons.append("coverage_below_threshold")
    if attempts < MIN_REFUTATION_ATTEMPTS:
        reasons.append("too_few_refutation_attempts")
    if ledger.counter == 0 and cp_upper(0, o) > 1 - tau_confidence:
        reasons.append("zero_counter_opportunity_too_small")
    if not negative_control_clean:
        reasons.append("negative_control_slice_not_clean")
    return Assessment(confidence_lower, coverage, upper, not reasons, tuple(reasons))


@dataclass(frozen=True, slots=True)
class CreationOutcome:
    lifecycle: str
    verified: bool
    channel: str


def creation_outcome(structurally_sound: bool) -> CreationOutcome:
    """An ``infer`` that passes the structural checks is stored unverified, as a hypothesis.

    The bound is deliberately *not* an input: below-τ candidates must exist to be tested.
    """
    if structurally_sound is not True:
        raise AdmissionError("structural_soundness_failed")
    require_transition(None, "inferred")
    return CreationOutcome("inferred", False, "hypotheses")


def may_verify(assessment: Assessment, *, held_out_passed: bool) -> bool:
    """``inferred → inferred(verified=true)``: admissible **and** held-out verification passed."""
    if not isinstance(held_out_passed, bool):
        raise AdmissionError("held_out_flag_must_be_boolean")
    require_transition("inferred", "inferred")
    return assessment.admissible and held_out_passed


__all__ = [
    "DEFAULT_TAU_CONFIDENCE",
    "DEFAULT_TAU_COVERAGE",
    "MIN_REFUTATION_ATTEMPTS",
    "TAIL_PROBABILITY",
    "AdmissionError",
    "Assessment",
    "CreationOutcome",
    "Ledger",
    "assess",
    "cp_upper",
    "creation_outcome",
    "may_verify",
]
