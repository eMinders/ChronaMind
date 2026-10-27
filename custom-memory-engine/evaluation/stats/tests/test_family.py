"""Exercise source pairing through bootstrap, global family and IUT conditions."""

import pytest
from pydantic import ValidationError

from evaluation.stats.analysis import ContrastAnalysis, analyze_contrast
from evaluation.stats.family import DecisionPlan, FamilyEvidence, evaluate_family
from evaluation.stats.models import Dataset, Endpoint, PairedObservation, PairedSample


def analysis(dataset: Dataset, endpoint: Endpoint, comparator: str, effect: float) -> ContrastAnalysis:
    return analyze_contrast(
        PairedSample(
            dataset=dataset,
            endpoint=endpoint,
            comparator=comparator,
            observations=tuple(
                PairedObservation(
                    cluster_id=str(i),
                    item_id=f"{endpoint}-{i}" if dataset == "T" else str(i),
                    run_id="r",
                    proposed=0.5,
                    comparator=0.5 - effect,
                )
                for i in range(8)
            ),
        ),
        seed=42,
    )


def plan() -> DecisionPlan:
    return DecisionPlan(
        freeze_reference="synthetic-unit-test-only",
        present_margin=0.125,
        luar_margin=0.125,
        h1_precision_adequate=True,
        h2_precision_adequate=True,
        h3_precision_adequate=True,
    )


@pytest.fixture(scope="module")
def complete_family() -> FamilyEvidence:
    return evaluate_family(
        analysis("T", "history_taa", "A2", 0.25),
        analysis("T", "present_taa", "A2", 0.0),
        analysis("LongMemEval_S", "recall", "A5", 0.25),
        analysis("LongMemEval_S", "recall", "A6", 0.125),
        analysis("G", "asr", "A3", -0.25),
        analysis("G", "luar", "A3", 0.0),
        plan=plan(),
    )


def test_complete_pipeline_global_family_iut_and_json_reproducibility(complete_family: FamilyEvidence) -> None:
    result = complete_family
    # All four raw superiority p=2/256. H2 first combines its two by max;
    # Holm over exactly three tests gives3/128, not4/128 or5/128.
    assert result.adjusted_p.h1 == result.adjusted_p.h2 == result.adjusted_p.h3 == 3 / 128
    assert result.h1.verdict == result.h2.verdict == "Supported"
    assert result.h3.feasible and result.h3.asr_superiority and result.h3.luar_noninferiority
    assert result.h3_decision.verdict == "Supported"
    assert FamilyEvidence.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("mutation", ["role", "freeze", "p", "precision", "h3_p", "h3_precision"])
def test_persisted_family_rejects_contradictory_roles_plan_and_p_values(
    complete_family: FamilyEvidence, mutation: str
) -> None:
    data = complete_family.model_dump()
    if mutation == "role":
        data["h1"]["hypothesis"] = "H4"
    elif mutation == "freeze":
        data["h1"]["freeze_reference"] = "a-different-freeze"
    elif mutation == "p":
        data["adjusted_p"]["h1"] = 0.99
    elif mutation == "precision":
        data["plan"]["h1_precision_adequate"] = False
    elif mutation == "h3_p":
        data["adjusted_p"]["h3"] = 0.99
    else:
        data["plan"]["h3_precision_adequate"] = False
    with pytest.raises(ValidationError):
        FamilyEvidence.model_validate(data)


def test_missing_superiority_test_cannot_shrink_the_global_family() -> None:
    with pytest.raises(ValueError, match="complete_confirmatory"):
        evaluate_family(
            analysis("T", "history_taa", "A2", 0.25),
            analysis("T", "present_taa", "A2", 0.0),
            analysis("LoCoMo", "recall", "B2", 0.25),
            analysis("LongMemEval_S", "recall", "A6", 0.25),
            analysis("G", "asr", "A3", -0.25),
            analysis("G", "luar", "A3", 0.0),
            plan=plan(),
        )


def test_plan_requires_explicit_margins_precision_and_freeze_reference() -> None:
    with pytest.raises(ValidationError):
        DecisionPlan.model_validate({})
    data = plan().model_dump()
    data["freeze_reference"] = " "
    with pytest.raises(ValidationError, match="nonblank"):
        DecisionPlan.model_validate(data)


def test_family_h3_decision_is_bound_to_conditions_and_freeze(complete_family: FamilyEvidence) -> None:
    data = complete_family.model_dump()
    data["h3_decision"] = None
    with pytest.raises(ValidationError, match="valid dictionary"):
        FamilyEvidence.model_validate(data)
    data = complete_family.model_dump()
    data["h3_decision"]["freeze_reference"] = "another-freeze"
    with pytest.raises(ValidationError, match="h3_decision_defined_conditions"):
        FamilyEvidence.model_validate(data)
