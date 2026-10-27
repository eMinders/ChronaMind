"""Decision truth tables use explicit synthetic evidence, never research results."""

from hashlib import sha256
from typing import TypedDict, cast

import pytest
from pydantic import ValidationError

from evaluation.stats.analysis import ContrastAnalysis, analyze_contrast
from evaluation.stats.bootstrap import BootstrapResult
from evaluation.stats.decisions import (
    H4Evidence,
    LeakageScenario,
    evaluate_h1,
    evaluate_h2,
    evaluate_h4,
    h3_conditions,
)
from evaluation.stats.models import ConfidenceInterval, Dataset, Endpoint, PairedObservation, PairedSample
from evaluation.stats.permutation import paired_permutation


class H1Setup(TypedDict):
    present_margin: float
    precision_adequate: bool
    freeze_reference: str


class H1Arguments(H1Setup):
    adjusted_p: float


class H2Arguments(TypedDict):
    adjusted_p: float
    precision_adequate: bool
    freeze_reference: str


class H4Arguments(TypedDict):
    informativeness_limit: float
    minimum_control_share: float
    frozen_preconditions_met: bool
    freeze_reference: str


def evidence(
    *,
    dataset: Dataset = "T",
    endpoint: Endpoint = "history_taa",
    comparator: str = "A2",
    estimate: float = 0.25,
    low: float = 0.1,
    high: float = 0.4,
    low95: float | None = None,
    high95: float | None = None,
    n: int = 7,
) -> ContrastAnalysis:
    """Isolate branch/boundary rules with explicit precomputed interval fixtures."""
    sample = PairedSample(
        dataset=dataset,
        endpoint=endpoint,
        comparator=comparator,
        observations=tuple(
            PairedObservation(
                cluster_id=str(i),
                item_id=f"{endpoint}-{i}" if dataset == "T" else str(i),
                run_id="r",
                proposed=0.5,
                comparator=0.5 - estimate,
            )
            for i in range(n)
        ),
    )
    return ContrastAnalysis(
        sample=sample,
        input_sha256=sha256(sample.model_dump_json().encode("utf-8")).hexdigest(),
        bootstrap=BootstrapResult(
            estimate=estimate,
            ci95=ConfidenceInterval(
                level=0.95, low=low if low95 is None else low95, high=high if high95 is None else high95
            ),
            conservative_ci=ConfidenceInterval(level=1 - 0.05 / 3, low=low, high=high),
            noninferiority_lower=low if low95 is None else low95,
            clusters=sample.cluster_effects(),
            seed=42,
            resamples=10_000,
        ),
        permutation=None if endpoint in {"present_taa", "luar"} else paired_permutation([estimate] * n, seed=42),
    )


def test_h1_iut_support_and_strict_p_and_ni_boundaries() -> None:
    history = evidence()
    present = evidence(endpoint="present_taa", estimate=0.0, low=-0.1, high=0.1)
    kwargs = H1Setup(present_margin=0.125, precision_adequate=True, freeze_reference="synthetic-freeze")
    assert evaluate_h1(history, present, adjusted_p=0.049, **kwargs).verdict == "Supported"
    assert evaluate_h1(history, present, adjusted_p=0.05, **kwargs).verdict == "Inconclusive"
    boundary = evidence(endpoint="present_taa", estimate=0.0, low=-0.125, high=0.125)
    assert evaluate_h1(history, boundary, adjusted_p=0.01, **kwargs).verdict == "Inconclusive"


def test_h1_rejection_boundaries_and_feasibility_override() -> None:
    present = evidence(endpoint="present_taa", estimate=0.0, low=-0.1, high=0.1)
    kwargs = H1Arguments(
        adjusted_p=0.01, present_margin=0.125, precision_adequate=True, freeze_reference="synthetic-freeze"
    )
    nonpositive = evidence(estimate=-0.25, low=-0.4, high=0.0)
    assert evaluate_h1(nonpositive, present, **kwargs).verdict == "Not supported"
    excessive_loss = evidence(endpoint="present_taa", estimate=-0.25, low=-0.4, high=-0.126)
    assert evaluate_h1(evidence(), excessive_loss, **kwargs).verdict == "Not supported"
    exact_loss = evidence(endpoint="present_taa", estimate=-0.25, low=-0.4, high=-0.125)
    assert evaluate_h1(evidence(), exact_loss, **kwargs).verdict == "Inconclusive"
    # The frozen floor applies to history, not the all-tied present-state NI endpoint.
    assert evaluate_h1(evidence(n=6), evidence(endpoint="present_taa", n=6), **kwargs).verdict == "Inconclusive"
    kwargs["precision_adequate"] = False
    assert evaluate_h1(evidence(), present, **kwargs).verdict == "Inconclusive"


def test_h1_conservative_lower_must_be_strictly_positive() -> None:
    result = evaluate_h1(
        evidence(low=0.0),
        evidence(endpoint="present_taa"),
        adjusted_p=0.001,
        present_margin=0.1,
        precision_adequate=True,
        freeze_reference="synthetic-freeze",
    )
    assert result.verdict == "Inconclusive"


def test_h2_both_contrasts_positive_global_p_and_rejection() -> None:
    dense = evidence(dataset="LongMemEval_S", endpoint="recall", comparator="A5")
    lexical = evidence(dataset="LongMemEval_S", endpoint="recall", comparator="A6")
    kwargs = H2Arguments(adjusted_p=0.01, precision_adequate=True, freeze_reference="synthetic-freeze")
    assert evaluate_h2(dense, lexical, **kwargs).verdict == "Supported"
    failing = evidence(dataset="LongMemEval_S", endpoint="recall", comparator="A6", estimate=-0.25, low=-0.4, high=0.0)
    assert evaluate_h2(dense, failing, **kwargs).verdict == "Not supported"
    # Proposal's explicit inconclusive cell covers an interval containing zero;
    # a significant p-value alone cannot override that cell.
    uncertain = evidence(dataset="LongMemEval_S", endpoint="recall", comparator="A6", low=-0.1)
    assert evaluate_h2(dense, uncertain, **kwargs).verdict == "Inconclusive"
    kwargs["adjusted_p"] = 0.05
    assert evaluate_h2(dense, lexical, **kwargs).verdict == "Inconclusive"


def test_h2_cluster_floor_and_shared_proposed_identity() -> None:
    dense = evidence(dataset="LongMemEval_S", endpoint="recall", comparator="A5", n=6)
    lexical = evidence(dataset="LongMemEval_S", endpoint="recall", comparator="A6", n=6)
    assert (
        evaluate_h2(dense, lexical, adjusted_p=0.001, precision_adequate=True, freeze_reference="synthetic").verdict
        == "Inconclusive"
    )
    wrong = evidence(dataset="LongMemEval_S", endpoint="recall", comparator="A6", n=7)
    with pytest.raises(ValueError, match="cluster_identity"):
        evaluate_h2(dense, wrong, adjusted_p=0.001, precision_adequate=True, freeze_reference="synthetic")


def test_h3_conditions_are_complete_without_protocol_placeholder() -> None:
    asr = evidence(dataset="G", endpoint="asr", comparator="A3", estimate=-0.25, low=-0.4, high=-0.1)
    luar = evidence(dataset="G", endpoint="luar", comparator="A3", estimate=0.0, low=-0.1, high=0.1)
    result = h3_conditions(asr, luar, adjusted_p=0.01, luar_margin=0.125, precision_adequate=True)
    assert result.feasible and result.asr_superiority and result.luar_noninferiority
    assert "clarification_required" not in result.model_dump()


def scenarios(n: int = 36, leaks: int = 0, control: int = 36) -> tuple[LeakageScenario, ...]:
    return tuple(
        LeakageScenario(scenario_id=str(i), leaked=i < leaks, positive_control_leaked=i < control) for i in range(n)
    )


def test_h4_three_conditions_and_leak_precedence() -> None:
    kwargs = H4Arguments(
        informativeness_limit=0.1,
        minimum_control_share=0.8,
        frozen_preconditions_met=True,
        freeze_reference="synthetic",
    )
    result = evaluate_h4(scenarios(control=29), **kwargs)
    assert result.decision.verdict == "Supported"
    assert result.upper == pytest.approx(1 - 0.025 ** (1 / 36))
    assert evaluate_h4(scenarios(control=28), **kwargs).decision.verdict == "Inconclusive"
    assert evaluate_h4(scenarios(n=35), **kwargs).decision.verdict == "Inconclusive"
    kwargs["frozen_preconditions_met"] = False
    assert evaluate_h4(scenarios(), **kwargs).decision.verdict == "Inconclusive"
    assert evaluate_h4(scenarios(leaks=1, control=0), **kwargs).decision.verdict == "Not supported"


def test_h4_equality_thresholds_support_and_duplicate_scenarios_fail() -> None:
    preliminary = evaluate_h4(
        scenarios(control=18),
        informativeness_limit=0.2,
        minimum_control_share=0.5,
        frozen_preconditions_met=True,
        freeze_reference="synthetic",
    )
    assert (
        evaluate_h4(
            scenarios(control=18),
            informativeness_limit=preliminary.upper,
            minimum_control_share=0.5,
            frozen_preconditions_met=True,
            freeze_reference="synthetic",
        ).decision.verdict
        == "Supported"
    )
    with pytest.raises(ValueError, match="unique_scenarios"):
        evaluate_h4(
            scenarios() + scenarios()[:1],
            informativeness_limit=0.1,
            minimum_control_share=0.8,
            frozen_preconditions_met=True,
            freeze_reference="synthetic",
        )


@pytest.mark.parametrize("mutation", ["n", "leak", "control", "share", "interval", "role", "precondition"])
def test_persisted_h4_rejects_contradictory_evidence(mutation: str) -> None:
    result = evaluate_h4(
        scenarios(),
        informativeness_limit=0.1,
        minimum_control_share=0.8,
        frozen_preconditions_met=True,
        freeze_reference="synthetic",
    )
    data = result.model_dump()
    if mutation == "n":
        data["n_scenarios"] = 0
    elif mutation == "leak":
        data["leaking_scenarios"] = 1
    elif mutation == "control":
        data["positive_control_scenarios"] = 37
    elif mutation == "share":
        data["positive_control_share"] = -1.0
    elif mutation == "interval":
        data["upper"] = 0.01
    elif mutation == "role":
        data["decision"]["hypothesis"] = "H1"
    else:
        data["frozen_preconditions_met"] = False
    with pytest.raises(ValidationError):
        H4Evidence.model_validate(data)


def test_real_analysis_pipeline_preserves_counts_hashes_and_locomo_gate() -> None:
    data = evidence().sample
    result = analyze_contrast(data, seed=13)
    assert result.permutation is not None
    assert result.bootstrap.estimate == result.permutation.estimate == 0.25
    assert result.permutation.p_value == 2 / 2**7
    assert result.inferentially_feasible
    assert len(result.input_sha256) == 64
    assert analyze_contrast(data, seed=13) == result
    locomo = PairedSample(dataset="LoCoMo", endpoint="qa", comparator="B1", observations=data.observations)
    descriptive = analyze_contrast(locomo, seed=13)
    assert not descriptive.inferentially_feasible
    assert descriptive.permutation is None


@pytest.mark.parametrize("value", [True, -0.01, 1.01, float("nan"), float("inf")])
def test_h1_invalid_probability(value: object) -> None:
    with pytest.raises(ValueError, match="adjusted_p"):
        evaluate_h1(
            evidence(),
            evidence(endpoint="present_taa"),
            adjusted_p=cast(float, value),
            present_margin=0.1,
            precision_adequate=True,
            freeze_reference="synthetic",
        )
