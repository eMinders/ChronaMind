"""B-07 / V2-D41: per-source-type grounding — valid, incomplete, contradictory, stale, unsupported."""

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from custom_memory_engine.engine.gate import evidence_supported
from custom_memory_engine.grounding.sources import Evidence, canonical_hash, ground

NOW = datetime(2026, 11, 1, tzinfo=UTC)
AGE = timedelta(days=30)
PAGE = "Release notes\r\nThe API rate limit is 600 requests per minute.\n"


def external(**overrides: object) -> Evidence:
    fields: dict[str, object] = {
        "source_type": "external",
        "ref": "https://example.test/notes",
        "span": "rate limit is 600 requests per minute",
        "content": PAGE,
        "content_hash": canonical_hash(PAGE),
        "fetched_at": NOW - timedelta(days=1),
    }
    return Evidence(**(fields | overrides))  # type: ignore[arg-type]


def turn(span: str, content: str, role: str = "user") -> Evidence:
    return Evidence("turn", "t1", span, content, role=role)


def run(*items: Evidence):
    return ground(items, now=NOW, max_external_age=AGE)


def test_valid_external_source_grounds_with_identity_and_boundary_label() -> None:
    result = run(external())
    assert result.grounded and result.reasons == ()
    record = result.sources[0]
    assert (record.source_type, record.ref, record.observed_at) == (
        "external",
        "https://example.test/notes",
        NOW - timedelta(days=1),
    )
    assert record.in_deletion_boundary is False  # imported content is outside the deletion boundary


def test_valid_user_turn_grounds_and_is_inside_the_deletion_boundary() -> None:
    result = run(turn("I am vegetarian", "Hi, I am vegetarian."))
    assert result.grounded and result.sources[0].in_deletion_boundary


def test_turn_grounding_is_identical_to_the_v1_gate() -> None:
    contents = ["I am vegetarian", "I eat fish now"]
    for span, content, role in itertools.product(["vegetarian", "fish", "beef", ""], contents, ["user", "assistant"]):
        expected = evidence_supported((span,), ((role, content),))
        assert run(turn(span, content, role)).grounded is expected, (span, content, role)


def test_assistant_turn_never_grounds_a_user_fact() -> None:
    assert run(turn("vegetarian", "You are vegetarian.", role="assistant")).reasons == ("span_not_in_user_turn",)


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"content_hash": None}, "incomplete_source"),
        ({"fetched_at": None}, "incomplete_source"),
        ({"ref": ""}, "incomplete_source"),
        ({"span": ""}, "incomplete_source"),
        ({"fetched_at": datetime(2026, 10, 30)}, "fetched_at_must_be_timezone_aware"),
    ],
)
def test_incomplete_external_sources_fail_closed(override: dict[str, object], reason: str) -> None:
    result = run(external(**override))
    assert not result.grounded and result.reasons == (reason,)


def test_contradictory_hash_fails_closed() -> None:
    """Content changed after the hash was declared: the span cannot be trusted."""
    tampered = PAGE.replace("600", "6000")
    result = run(external(content=tampered, span="rate limit is 6000 requests per minute"))
    assert result.reasons == ("content_hash_mismatch",)


def test_span_absent_from_matching_content_fails() -> None:
    assert run(external(span="no rate limit")).reasons == ("span_not_in_source_content",)


def test_stale_and_future_sources_fail_closed() -> None:
    assert run(external(fetched_at=NOW - AGE - timedelta(seconds=1))).reasons == ("stale_source",)
    assert run(external(fetched_at=NOW - AGE)).grounded  # boundary is inclusive
    assert run(external(fetched_at=NOW + timedelta(seconds=1))).reasons == ("fetched_at_in_future",)


def test_inferred_artifacts_are_never_evidence() -> None:
    inferred = Evidence("artifact", "abs-1", "user prefers tea", "user prefers tea")
    assert run(inferred).reasons == ("inferred_artifact_is_not_evidence",)


@pytest.mark.parametrize("source_type", ["url", "TURN", ""])
def test_unsupported_source_types_fail_closed(source_type: str) -> None:
    assert run(Evidence(source_type, "r", "x", "x")).reasons == ("unsupported_source_type",)


def test_every_item_must_ground_one_bad_item_poisons_the_set() -> None:
    result = run(turn("I am vegetarian", "I am vegetarian"), external(content_hash="0" * 64))
    assert not result.grounded and result.sources == ()
    assert result.reasons == ("content_hash_mismatch",)


def test_empty_evidence_never_grounds() -> None:
    assert run().reasons == ("no_evidence",)


def test_canonical_hash_is_line_ending_and_normalization_stable() -> None:
    assert canonical_hash("a\r\nb") == canonical_hash("a\nb") == canonical_hash("a\rb")
    assert canonical_hash("caf\u00e9") == canonical_hash("cafe\u0301")


def test_policy_inputs_are_validated() -> None:
    with pytest.raises(ValueError):
        ground([external()], now=datetime(2026, 11, 1), max_external_age=AGE)
    with pytest.raises(ValueError):
        ground([external()], now=NOW, max_external_age=timedelta(0))
