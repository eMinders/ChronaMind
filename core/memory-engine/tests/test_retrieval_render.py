"""Rank-fusion and render rules: the deterministic half of the read path."""

from datetime import UTC, datetime
from typing import Literal

import pytest

from custom_memory_engine.engine.predicates import TemporalVersion, TimeRange
from custom_memory_engine.engine.render import (
    RenderError,
    default_token_counter,
    is_renderable,
    render_context,
)
from custom_memory_engine.engine.retrieval import RetrievalError, fusion_order, rrf_score


def version(
    version_id: str,
    content: str,
    *,
    status: Literal["active", "archived"] = "active",
    valid_from: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    valid_until: datetime | None = None,
    user: str = "u1",
) -> TemporalVersion:
    return TemporalVersion(
        id=version_id,
        memory_key="diet",
        user_id=user,
        status=status,  # type: ignore[arg-type]
        subject_key="user",
        predicate_key="diet",
        single_valued=True,
        content=content,
        event_range=TimeRange(lower=valid_from, upper=valid_until, lower_inclusive=True, upper_inclusive=False),
        recorded_range=TimeRange(
            lower=datetime(2026, 2, 1, tzinfo=UTC), upper=None, lower_inclusive=True, upper_inclusive=False
        ),
        layer="semantic",
        valid_from_basis="explicit",
        assertion_mode="asserted",
        confidence=0.9,
        importance=0.7,
        reinforcement_count=0,
        extractor_version="test",
        archival_branch=None,
    )


def test_rrf_score_matches_spec_formula() -> None:
    assert rrf_score(1, None) == pytest.approx(1 / 61)
    assert rrf_score(None, 2) == pytest.approx(1 / 62)
    assert rrf_score(1, 1) == pytest.approx(2 / 61)
    assert rrf_score(None, None) == 0.0


def test_fusion_ranks_every_candidate_and_breaks_ties_by_id() -> None:
    fused = fusion_order(("a", "b"), ("b", "a"))
    # Both appear once at rank 1 and once at rank 2, so scores tie on 1/61 + 1/62.
    assert [version_id for version_id, _ in fused] == ["a", "b"]
    assert fused[0][1] == pytest.approx(fused[1][1])


def test_fusion_rewards_candidates_hit_by_both_channels() -> None:
    fused = fusion_order(("dense-only", "both"), ("lexical-only", "both"))
    order = [version_id for version_id, _ in fused]
    assert order[0] == "both"
    assert set(order[1:]) == {"dense-only", "lexical-only"}


def test_ablation_channel_modes_drop_the_other_channel() -> None:
    assert [v for v, _ in fusion_order(("a",), ("b",), mode="dense")] == ["a"]
    assert [v for v, _ in fusion_order(("a",), ("b",), mode="lexical")] == ["b"]


def test_fusion_rejects_impossible_constants_and_modes() -> None:
    with pytest.raises(RetrievalError):
        fusion_order(("a",), ("a",), k=0)
    with pytest.raises(RetrievalError):
        fusion_order(("a",), ("a",), mode="weighted_sum")  # type: ignore[arg-type]


def test_fusion_of_empty_channels_is_empty() -> None:
    assert fusion_order((), ()) == ()


def test_render_drops_superseded_and_archived_versions() -> None:
    rendered = render_context(
        (version("v1", "vegetarian"), version("v2", "fish", status="archived")),
        mode="current",
        token_budget=100,
    )
    assert [item.version.id for item in rendered.items] == ["v1"]
    assert rendered.truncated_items == 1


def test_current_mode_omits_ended_versions_but_historical_keeps_them() -> None:
    ended = version("old", "vegetarian", valid_until=datetime(2026, 7, 15, tzinfo=UTC))
    reference = datetime(2026, 10, 1, tzinfo=UTC)
    current = render_context((ended,), mode="current", token_budget=100, reference_time=reference)
    historical = render_context((ended,), mode="as_of", token_budget=100, reference_time=reference)
    assert current.items == ()
    assert [item.label for item in historical.items] == ["previous"]


def test_render_stops_at_budget_and_preserves_rank_order() -> None:
    items = tuple(version(f"v{i}", f"memory number {i}") for i in range(5))
    budget = 3 * default_token_counter("[current | valid 2026-01-01T00:00:00+00:00..open] memory number 0")
    rendered = render_context(items, mode="current", token_budget=budget)
    assert [item.version.id for item in rendered.items] == ["v0", "v1", "v2"]
    assert rendered.tokens <= budget
    assert rendered.truncated_items == 2
    assert rendered.text.splitlines()[0].startswith("[current | valid 2026-01-01")


def test_render_never_exceeds_budget_for_one_oversized_item() -> None:
    rendered = render_context((version("big", "x " * 500),), mode="current", token_budget=5)
    assert rendered.items == ()
    assert rendered.tokens == 0
    assert rendered.truncated_tokens > 0


def test_render_rejects_negative_budget() -> None:
    with pytest.raises(RenderError):
        render_context((), mode="current", token_budget=-1)


def test_renderable_requires_active_status() -> None:
    assert is_renderable(version("v1", "x"), mode="current", reference_time=None)
    assert not is_renderable(version("v1", "x", status="archived"), mode="current", reference_time=None)
    assert not is_renderable(version("v1", "x", status="archived"), mode="as_of", reference_time=None)
