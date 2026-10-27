"""Known H3 branches are executable; undefined cases never acquire a verdict."""

import pytest
from pydantic import ValidationError

from evaluation.stats.analysis import ContrastAnalysis, analyze_contrast
from evaluation.stats.decisions import H3Assessment, evaluate_h3
from evaluation.stats.models import PairedObservation, PairedSample


def contrast(endpoint: str, effects: tuple[float, ...]) -> ContrastAnalysis:
    return analyze_contrast(
        PairedSample.model_validate(
            dict(
                dataset="G",
                endpoint=endpoint,
                comparator="A3",
                observations=tuple(
                    PairedObservation(
                        cluster_id=str(i),
                        item_id=f"{endpoint}-{i}",
                        run_id="r",
                        proposed=max(effect, 0),
                        comparator=max(-effect, 0),
                    )
                    for i, effect in enumerate(effects)
                ),
            )
        ),
        seed=42,
    )


@pytest.mark.parametrize(
    ("asr", "luar", "precision", "expected"),
    [
        ((-0.25,) * 8, (0.0,) * 8, True, "Supported"),
        ((-0.25,) * 8, (-0.25,) * 8, True, "Not supported"),
        ((-0.25,) * 6, (0.0,) * 6, True, "Inconclusive"),
        ((-0.25,) * 8, (0.0,) * 8, False, "Inconclusive"),
        ((-0.25,) * 6, (-0.25,) * 6, True, "Inconclusive"),
        ((-0.25,) * 8, (-0.25,) * 4 + (0.0,) * 4, True, "Inconclusive"),
    ],
)
def test_explicit_h3_branches(asr: tuple[float, ...], luar: tuple[float, ...], precision: bool, expected: str) -> None:
    result = evaluate_h3(
        contrast("asr", asr),
        contrast("luar", luar),
        adjusted_p=0.01,
        luar_margin=0.125,
        precision_adequate=precision,
        freeze_reference="synthetic",
    )
    assert result.decision is not None
    assert result.decision.hypothesis == "H3" and result.decision.verdict == expected
    assert not result.case_requires_clarification
    # The overall protocol still needs the leader, even for a defined case.
    assert result.conditions.clarification_required
    assert H3Assessment.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    ("asr", "luar", "adjusted_p"),
    [
        ((-0.25,) * 6 + (0.5,) * 2, (0.0,) * 8, 0.2),
        ((-0.25,) * 4 + (0.25,) * 4, (0.0,) * 8, 1.0),
        ((0.25,) * 8, (0.0,) * 8, 0.01),
        ((-0.25,) * 8, (-0.125,) * 8, 0.01),
        ((-0.25,) * 8, (0.0,) * 8, 0.05),
    ],
)
def test_undefined_cases_remain_pending(asr: tuple[float, ...], luar: tuple[float, ...], adjusted_p: float) -> None:
    result = evaluate_h3(
        contrast("asr", asr),
        contrast("luar", luar),
        adjusted_p=adjusted_p,
        luar_margin=0.125,
        precision_adequate=True,
        freeze_reference="synthetic",
    )
    assert result.decision is None and result.case_requires_clarification
    assert H3Assessment.model_validate_json(result.model_dump_json()) == result
    altered = result.model_dump()
    altered["decision"] = dict(
        hypothesis="H3",
        verdict="Inconclusive",
        reasons=("unapproved_rule",),
        freeze_reference="synthetic",
    )
    with pytest.raises(ValidationError, match="defined_conditions_mismatch"):
        H3Assessment.model_validate(altered)


def test_known_case_cannot_be_suppressed_or_relabelled_in_json() -> None:
    result = evaluate_h3(
        contrast("asr", (-0.25,) * 8),
        contrast("luar", (0.0,) * 8),
        adjusted_p=0.01,
        luar_margin=0.125,
        precision_adequate=True,
        freeze_reference="synthetic",
    )
    altered = result.model_dump()
    altered["decision"] = None
    with pytest.raises(ValidationError, match="defined_conditions_mismatch"):
        H3Assessment.model_validate(altered)
    for key, value in (("hypothesis", "H1"), ("verdict", "Not supported"), ("freeze_reference", "another-freeze")):
        altered = result.model_dump()
        altered["decision"][key] = value
        with pytest.raises(ValidationError, match="defined_conditions_mismatch"):
            H3Assessment.model_validate(altered)
