"""Independent source-mix and contradictory persisted-evidence regressions."""

import pytest
from pydantic import ValidationError

from evaluation.stats.analysis import ContrastAnalysis, analyze_contrast
from evaluation.stats.bootstrap import BootstrapResult
from evaluation.stats.models import Endpoint, PairedObservation, PairedSample


def paired(values: tuple[float, ...], endpoint: Endpoint = "history_taa") -> PairedSample:
    return PairedSample(
        dataset="T",
        endpoint=endpoint,
        comparator="A2",
        observations=tuple(
            PairedObservation(
                cluster_id=str(i), item_id=str(i), run_id="r", proposed=max(value, 0), comparator=max(-value, 0)
            )
            for i, value in enumerate(values)
        ),
    )


def test_reject_cross_source_test_even_with_matching_effect_and_direction_counts() -> None:
    first = analyze_contrast(paired((1.0, 0.125, 0.125, 0.125, 0.125, 0.125, -0.625)), seed=42)
    second = analyze_contrast(paired((0.25, 0.25, 0.25, 0.25, 0.25, 0.25, -0.5)), seed=42)
    assert first.permutation is not None and second.permutation is not None
    assert first.permutation.estimate == second.permutation.estimate
    assert first.permutation.n_positive == second.permutation.n_positive == 6
    assert first.permutation.p_value != second.permutation.p_value
    altered = first.model_dump()
    altered["permutation"] = second.permutation.model_dump()
    with pytest.raises(ValidationError, match="summary_mismatch"):
        ContrastAnalysis.model_validate(altered)


@pytest.mark.parametrize("field", ["estimate", "noninferiority_lower", "ci95", "conservative_ci", "clusters"])
def test_reject_inconsistent_bootstrap_summary(field: str) -> None:
    original = analyze_contrast(paired((0.25,) * 7), seed=42).bootstrap
    altered = original.model_dump()
    replacements = {
        "estimate": 0.5,
        "noninferiority_lower": 0.1,
        "ci95": dict(level=0.90, low=0.25, high=0.25),
        "conservative_ci": dict(level=1 - 0.05 / 3, low=0.3, high=0.4),
        "clusters": original.model_dump()["clusters"] + original.model_dump()["clusters"][:1],
    }
    altered[field] = replacements[field]
    with pytest.raises(ValidationError):
        BootstrapResult.model_validate(altered)


def test_input_hash_and_raw_cluster_binding_are_validated() -> None:
    original = analyze_contrast(paired((0.25,) * 7), seed=42)
    altered = original.model_dump()
    altered["input_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="input_hash"):
        ContrastAnalysis.model_validate(altered)
    altered = original.model_dump()
    altered["bootstrap"]["clusters"][0]["item_count"] = 2
    with pytest.raises(ValidationError, match="sample_cluster"):
        ContrastAnalysis.model_validate(altered)


def test_ni_endpoints_have_bootstrap_bounds_without_an_extra_superiority_test() -> None:
    result = analyze_contrast(paired((0.0,) * 7, endpoint="present_taa"), seed=42)
    assert result.permutation is None
    assert result.bootstrap.noninferiority_lower == 0
    assert not result.inferentially_feasible


def test_canonical_hash_survives_row_order_and_json_roundtrip() -> None:
    original = paired((0.25,) * 7)
    reordered = PairedSample(
        dataset="T", endpoint="history_taa", comparator="A2", observations=original.observations[::-1]
    )
    first, second = analyze_contrast(original, seed=42), analyze_contrast(reordered, seed=42)
    assert first == second
    assert ContrastAnalysis.model_validate_json(first.model_dump_json()) == first


def test_blank_ids_and_freeform_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="nonblank_identifier"):
        PairedObservation(cluster_id=" ", item_id="q", run_id="r", proposed=0.5, comparator=0.5)
    data = paired((0.25,)).model_dump()
    data["margin"] = 0.05
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PairedSample.model_validate(data)
