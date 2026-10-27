"""Mechanical decision conditions from Proposal §§4.1/10.6.

H3's ambiguous ASR cases await the leader's clarification. Its component
conditions are exposed without inventing a final verdict for those cases.
"""

from math import isfinite
from typing import Literal

from pydantic import Field, model_validator

from evaluation.stats.analysis import ContrastAnalysis
from evaluation.stats.clopper_pearson import cp_interval
from evaluation.stats.models import StatsRecord

type Verdict = Literal["Supported", "Not supported", "Inconclusive"]


class Decision(StatsRecord):
    hypothesis: Literal["H1", "H2", "H4"]
    verdict: Verdict
    reasons: tuple[str, ...] = Field(min_length=1)
    freeze_reference: str = Field(min_length=1)


def _probability(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1 or not isfinite(value):
        raise ValueError(f"invalid_{name}")


def _check_contrast(analysis: ContrastAnalysis, dataset: str, endpoint: str, comparator: str) -> None:
    if (analysis.sample.dataset, analysis.sample.endpoint, analysis.sample.comparator) != (
        dataset,
        endpoint,
        comparator,
    ):
        raise ValueError("wrong_confirmatory_contrast")


def _check_joint_clusters(first: ContrastAnalysis, second: ContrastAnalysis) -> None:
    if tuple(c.cluster_id for c in first.bootstrap.clusters) != tuple(c.cluster_id for c in second.bootstrap.clusters):
        raise ValueError("co_primary_cluster_identity_mismatch")
    if {r.run_id for r in first.sample.observations} != {r.run_id for r in second.sample.observations}:
        raise ValueError("co_primary_run_identity_mismatch")


def evaluate_h1(
    history: ContrastAnalysis,
    present: ContrastAnalysis,
    *,
    adjusted_p: float,
    present_margin: float,
    precision_adequate: bool,
    freeze_reference: str,
) -> Decision:
    """History superiority IUT present-state NI; strict support and loss boundaries."""
    _check_contrast(history, "T", "history_taa", "A2")
    _check_contrast(present, "T", "present_taa", "A2")
    _check_joint_clusters(history, present)
    history_questions = {(row.cluster_id, row.item_id) for row in history.sample.observations}
    present_questions = {(row.cluster_id, row.item_id) for row in present.sample.observations}
    if history_questions & present_questions:
        raise ValueError("h1_question_cannot_belong_to_both_strata")
    _probability(adjusted_p, "adjusted_p")
    _probability(present_margin, "present_margin")
    if type(precision_adequate) is not bool:
        raise ValueError("explicit_precision_assessment_required")
    if not history.inferentially_feasible or not precision_adequate:
        verdict, reasons = "Inconclusive", ("insufficient_independent_clusters_or_precision",)
    elif history.bootstrap.conservative_ci.high <= 0 or present.bootstrap.ci95.high < -present_margin:
        verdict, reasons = "Not supported", ("history_not_positive_or_present_loss_exceeds_margin",)
    elif (
        history.bootstrap.estimate > 0
        and adjusted_p < 0.05
        and history.bootstrap.conservative_ci.low > 0
        and present.bootstrap.noninferiority_lower > -present_margin
    ):
        verdict, reasons = "Supported", ("history_superiority_and_present_noninferiority",)
    else:
        verdict, reasons = "Inconclusive", ("co_primary_support_not_established",)
    return Decision(hypothesis="H1", verdict=verdict, reasons=reasons, freeze_reference=freeze_reference)


def evaluate_h2(
    dense: ContrastAnalysis,
    lexical: ContrastAnalysis,
    *,
    adjusted_p: float,
    precision_adequate: bool,
    freeze_reference: str,
) -> Decision:
    """Both positive contrasts and global Holm(max-p); no second within-H2 correction.

    Proposal §4.1 also explicitly assigns zero-containing intervals to the
    inconclusive outcome. Requiring both intervals above zero reconciles all
    three cells, as Technical Specification §9.3 makes explicit.
    """
    _check_contrast(dense, "LongMemEval_S", "recall", "A5")
    _check_contrast(lexical, "LongMemEval_S", "recall", "A6")
    _check_joint_clusters(dense, lexical)
    left = {(r.cluster_id, r.item_id, r.run_id): r.proposed for r in dense.sample.observations}
    right = {(r.cluster_id, r.item_id, r.run_id): r.proposed for r in lexical.sample.observations}
    if left != right:
        raise ValueError("h2_shared_proposed_observations_mismatch")
    _probability(adjusted_p, "adjusted_p")
    if type(precision_adequate) is not bool:
        raise ValueError("explicit_precision_assessment_required")
    if not dense.inferentially_feasible or not lexical.inferentially_feasible or not precision_adequate:
        verdict, reasons = "Inconclusive", ("insufficient_independent_clusters_or_precision",)
    elif dense.bootstrap.conservative_ci.high <= 0 or lexical.bootstrap.conservative_ci.high <= 0:
        verdict, reasons = "Not supported", ("a_retrieval_contrast_is_nonpositive",)
    elif (
        dense.bootstrap.estimate > 0
        and lexical.bootstrap.estimate > 0
        and dense.bootstrap.conservative_ci.low > 0
        and lexical.bootstrap.conservative_ci.low > 0
        and adjusted_p < 0.05
    ):
        verdict, reasons = "Supported", ("both_retrieval_contrasts_and_global_holm",)
    else:
        verdict, reasons = "Inconclusive", ("joint_retrieval_superiority_not_established",)
    return Decision(hypothesis="H2", verdict=verdict, reasons=reasons, freeze_reference=freeze_reference)


class H3Conditions(StatsRecord):
    """Computed conditions only; unresolved protocol cases have no invented verdict."""

    feasible: bool
    asr_superiority: bool
    luar_noninferiority: bool
    asr_interval_nonnegative: bool
    luar_interval_beyond_margin: bool
    clarification_required: Literal[True] = True

    @model_validator(mode="after")
    def noncontradictory_conditions(self) -> "H3Conditions":
        if self.asr_superiority and self.asr_interval_nonnegative:
            raise ValueError("contradictory_asr_conditions")
        if self.luar_noninferiority and self.luar_interval_beyond_margin:
            raise ValueError("contradictory_luar_conditions")
        return self


def h3_conditions(
    asr: ContrastAnalysis,
    luar: ContrastAnalysis,
    *,
    adjusted_p: float,
    luar_margin: float,
    precision_adequate: bool,
) -> H3Conditions:
    _check_contrast(asr, "G", "asr", "A3")
    _check_contrast(luar, "G", "luar", "A3")
    _check_joint_clusters(asr, luar)
    _probability(adjusted_p, "adjusted_p")
    _probability(luar_margin, "luar_margin")
    if type(precision_adequate) is not bool:
        raise ValueError("explicit_precision_assessment_required")
    return H3Conditions(
        feasible=asr.inferentially_feasible and precision_adequate,
        asr_superiority=asr.bootstrap.estimate < 0 and adjusted_p < 0.05 and asr.bootstrap.conservative_ci.high < 0,
        luar_noninferiority=luar.bootstrap.noninferiority_lower > -luar_margin,
        asr_interval_nonnegative=asr.bootstrap.conservative_ci.low >= 0,
        luar_interval_beyond_margin=luar.bootstrap.ci95.high < -luar_margin,
    )


class LeakageScenario(StatsRecord):
    """OR over all events/channels/probes within one independent scenario."""

    scenario_id: str = Field(min_length=1)
    leaked: bool
    positive_control_leaked: bool


class H4Evidence(StatsRecord):
    decision: Decision
    n_scenarios: int = Field(ge=1)
    leaking_scenarios: int = Field(ge=0)
    positive_control_scenarios: int = Field(ge=0)
    positive_control_share: float = Field(ge=0, le=1)
    lower: float = Field(ge=0, le=1)
    upper: float = Field(ge=0, le=1)
    informativeness_limit: float = Field(ge=0, le=1)
    minimum_control_share: float = Field(ge=0, le=1)
    frozen_preconditions_met: bool

    @model_validator(mode="after")
    def consistent_scenario_summary(self) -> "H4Evidence":
        if self.leaking_scenarios > self.n_scenarios or self.positive_control_scenarios > self.n_scenarios:
            raise ValueError("scenario_count_exceeds_denominator")
        if self.positive_control_share != self.positive_control_scenarios / self.n_scenarios:
            raise ValueError("control_share_count_mismatch")
        if (self.lower, self.upper) != cp_interval(self.leaking_scenarios, self.n_scenarios):
            raise ValueError("scenario_confidence_interval_mismatch")
        if self.leaking_scenarios:
            expected = "Not supported"
        elif (
            self.upper <= self.informativeness_limit
            and self.positive_control_share >= self.minimum_control_share
            and self.frozen_preconditions_met
        ):
            expected = "Supported"
        else:
            expected = "Inconclusive"
        if self.decision.hypothesis != "H4" or self.decision.verdict != expected:
            raise ValueError("h4_verdict_scenario_evidence_mismatch")
        return self


def evaluate_h4(
    scenarios: tuple[LeakageScenario, ...],
    *,
    informativeness_limit: float,
    minimum_control_share: float,
    frozen_preconditions_met: bool,
    freeze_reference: str,
) -> H4Evidence:
    """Any confirmed in-boundary leak rejects; zero leaks alone never suffice."""
    if not scenarios or len({row.scenario_id for row in scenarios}) != len(scenarios):
        raise ValueError("nonempty_unique_scenarios_required")
    _probability(informativeness_limit, "informativeness_limit")
    _probability(minimum_control_share, "minimum_control_share")
    if type(frozen_preconditions_met) is not bool:
        raise ValueError("explicit_frozen_preconditions_required")
    leaks = sum(row.leaked for row in scenarios)
    control = sum(row.positive_control_leaked for row in scenarios)
    lower, upper = cp_interval(leaks, len(scenarios))
    if leaks:
        verdict, reasons = "Not supported", ("confirmed_in_boundary_leak",)
    elif (
        upper <= informativeness_limit
        and control / len(scenarios) >= minimum_control_share
        and frozen_preconditions_met
    ):
        verdict, reasons = "Supported", ("no_observed_leak_informative_bound_and_sensitive_probes",)
    else:
        verdict, reasons = "Inconclusive", ("bound_control_or_frozen_precondition_failed",)
    return H4Evidence(
        decision=Decision(hypothesis="H4", verdict=verdict, reasons=reasons, freeze_reference=freeze_reference),
        n_scenarios=len(scenarios),
        leaking_scenarios=leaks,
        positive_control_scenarios=control,
        positive_control_share=control / len(scenarios),
        lower=lower,
        upper=upper,
        informativeness_limit=informativeness_limit,
        minimum_control_share=minimum_control_share,
        frozen_preconditions_met=frozen_preconditions_met,
    )
