"""Assemble the fixed global family without silently testing a smaller subset."""

from pydantic import Field, model_validator

from evaluation.stats.analysis import ContrastAnalysis
from evaluation.stats.decisions import (
    Decision,
    H3Conditions,
    defined_h3_decision,
    evaluate_h1,
    evaluate_h2,
    h3_conditions,
)
from evaluation.stats.models import StatsRecord
from evaluation.stats.multiplicity import confirmatory_holm


class FamilyPValues(StatsRecord):
    h1: float = Field(ge=0, le=1)
    h2: float = Field(ge=0, le=1)
    h3: float = Field(ge=0, le=1)


class DecisionPlan(StatsRecord):
    """Caller-supplied frozen values and pilot precision assessments; no defaults."""

    freeze_reference: str = Field(min_length=1)
    present_margin: float = Field(ge=0, le=1)
    luar_margin: float = Field(ge=0, le=1)
    h1_precision_adequate: bool
    h2_precision_adequate: bool
    h3_precision_adequate: bool


class FamilyEvidence(StatsRecord):
    """H3 final classification awaits protocol clarification; no completion claim."""

    plan: DecisionPlan
    adjusted_p: FamilyPValues
    h1: Decision
    h2: Decision
    h3: H3Conditions
    h3_decision: Decision | None

    @model_validator(mode="after")
    def consistent_decisions(self) -> "FamilyEvidence":
        for decision, hypothesis, adjusted_p, precision in (
            (self.h1, "H1", self.adjusted_p.h1, self.plan.h1_precision_adequate),
            (self.h2, "H2", self.adjusted_p.h2, self.plan.h2_precision_adequate),
        ):
            if decision.hypothesis != hypothesis or decision.freeze_reference != self.plan.freeze_reference:
                raise ValueError("decision_role_or_freeze_mismatch")
            if decision.verdict == "Supported" and (adjusted_p >= 0.05 or not precision):
                raise ValueError("supported_decision_conflicts_with_global_p_or_precision")
        if self.h3.asr_superiority and self.adjusted_p.h3 >= 0.05:
            raise ValueError("h3_superiority_conflicts_with_global_p")
        if self.h3.feasible and not self.plan.h3_precision_adequate:
            raise ValueError("h3_feasibility_conflicts_with_precision")
        if self.h3_decision != defined_h3_decision(self.h3, freeze_reference=self.plan.freeze_reference):
            raise ValueError("h3_decision_defined_conditions_mismatch")
        return self


def evaluate_family(
    history: ContrastAnalysis,
    present: ContrastAnalysis,
    dense: ContrastAnalysis,
    lexical: ContrastAnalysis,
    asr: ContrastAnalysis,
    luar: ContrastAnalysis,
    *,
    plan: DecisionPlan,
) -> FamilyEvidence:
    """Use all four raw superiority p-values to form exactly three Holm tests.

    The NI endpoints have no separate superiority p-values or Holm correction.
    This routine consumes analysis evidence only; it authorizes no research run.
    """
    if any(analysis.permutation is None for analysis in (history, dense, lexical, asr)):
        raise ValueError("complete_confirmatory_superiority_family_required")
    # Explicit narrowing keeps a missing inferential input from becoming p=0/NaN.
    assert history.permutation is not None
    assert dense.permutation is not None
    assert lexical.permutation is not None
    assert asr.permutation is not None
    adjusted = confirmatory_holm(
        h1_history=history.permutation.p_value,
        h2_dense=dense.permutation.p_value,
        h2_lexical=lexical.permutation.p_value,
        h3_asr=asr.permutation.p_value,
    )
    h3 = h3_conditions(
        asr,
        luar,
        adjusted_p=adjusted["H3"],
        luar_margin=plan.luar_margin,
        precision_adequate=plan.h3_precision_adequate,
    )
    return FamilyEvidence(
        plan=plan,
        adjusted_p=FamilyPValues(h1=adjusted["H1"], h2=adjusted["H2"], h3=adjusted["H3"]),
        h1=evaluate_h1(
            history,
            present,
            adjusted_p=adjusted["H1"],
            present_margin=plan.present_margin,
            precision_adequate=plan.h1_precision_adequate,
            freeze_reference=plan.freeze_reference,
        ),
        h2=evaluate_h2(
            dense,
            lexical,
            adjusted_p=adjusted["H2"],
            precision_adequate=plan.h2_precision_adequate,
            freeze_reference=plan.freeze_reference,
        ),
        h3=h3,
        h3_decision=defined_h3_decision(h3, freeze_reference=plan.freeze_reference),
    )
