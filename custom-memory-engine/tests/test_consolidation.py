"""RL-061 planning rules, without a database.

``plan_consolidation`` is the only place the branch-``(C)`` coverage contract and the
``depth <= 3`` budget are decided, so it is tested exhaustively here rather than only
through the integration path.
"""

from datetime import UTC, datetime
from typing import cast

import pytest

from custom_memory_engine.consolidation import (
    MAX_DERIVATION_DEPTH,
    ConsolidationError,
    SourceVersion,
    plan_consolidation,
)
from custom_memory_engine.consolidation.consolidator import ConsolidationLayer, plan_to_candidate

START = datetime(2026, 3, 10, tzinfo=UTC)
APRIL = datetime(2026, 4, 1, tzinfo=UTC)
JUNE = datetime(2026, 6, 1, tzinfo=UTC)


def source(
    version_id: str = "e1",
    *,
    valid_from: datetime = START,
    valid_until: datetime | None = None,
    depth: int = 0,
    user_id: str = "u1",
    subject_key: str = "user",
    predicate_key: str = "diet",
    memory_key: str = "user:diet",
) -> SourceVersion:
    return SourceVersion(
        version_id=version_id,
        memory_key=memory_key,
        user_id=user_id,
        subject_key=subject_key,
        predicate_key=predicate_key,
        status="active",
        layer="episodic",
        single_valued=False,
        has_open_recorded_range=True,
        valid_from=valid_from,
        valid_until=valid_until,
        depth=depth,
    )


def test_plan_covers_a_single_open_ended_source() -> None:
    plan = plan_consolidation((source(),), user_id="u1", content="Vegetarian", version_id="c1")
    assert plan.valid_from == START
    assert plan.valid_until is None, "an open-ended source must stay open-ended"
    assert plan.source_version_ids == ("e1",)
    assert plan.layer == "semantic"
    assert plan.derivation_type == "consolidation"


def test_plan_spanning_two_closed_sources_covers_the_union() -> None:
    plan = plan_consolidation(
        (
            source("e1", valid_from=START, valid_until=APRIL),
            source("e2", valid_from=APRIL, valid_until=JUNE),
        ),
        user_id="u1",
        content="Vegetarian",
        version_id="c1",
    )
    assert (plan.valid_from, plan.valid_until) == (START, JUNE)


def test_plan_extends_to_unbounded_if_any_source_is_unbounded() -> None:
    plan = plan_consolidation(
        (
            source("e1", valid_from=START, valid_until=APRIL),
            source("e2", valid_from=APRIL, valid_until=None),
        ),
        user_id="u1",
        content="Vegetarian",
        version_id="c1",
    )
    assert plan.valid_until is None


def test_plan_earliest_lower_bound_wins() -> None:
    plan = plan_consolidation(
        (source("e1", valid_from=APRIL), source("e2", valid_from=START)),
        user_id="u1",
        content="Vegetarian",
        version_id="c1",
    )
    assert plan.valid_from == START


def test_plan_depth_is_one_above_the_deepest_source() -> None:
    plan = plan_consolidation((source(depth=2),), user_id="u1", content="x", version_id="c1")
    assert plan.source_depth == MAX_DERIVATION_DEPTH


def test_plan_refuses_to_exceed_the_depth_budget() -> None:
    with pytest.raises(ConsolidationError, match="derivation_depth_exceeded"):
        plan_consolidation((source(depth=MAX_DERIVATION_DEPTH),), user_id="u1", content="x", version_id="c1")


def test_plan_derives_a_version_id_when_absent() -> None:
    plan = plan_consolidation((source(),), user_id="u1", content="x")
    assert plan.version_id.startswith("c-")


def test_plan_rejects_a_version_id_equal_to_its_source() -> None:
    with pytest.raises(ConsolidationError, match="derived_version_must_differ_from_sources"):
        plan_consolidation((source(),), user_id="u1", content="x", version_id="e1")


def test_plan_rejects_duplicate_sources() -> None:
    with pytest.raises(ConsolidationError, match="duplicate_source_version_id"):
        plan_consolidation((source(), source()), user_id="u1", content="x", version_id="c1")


def test_plan_rejects_a_foreign_tenant_source() -> None:
    with pytest.raises(ConsolidationError, match="source_versions_must_share_one_user"):
        plan_consolidation((source(user_id="u2"),), user_id="u1", content="x", version_id="c1")


def test_plan_rejects_sources_spanning_predicates() -> None:
    with pytest.raises(ConsolidationError, match="sources_must_share_subject_and_predicate"):
        plan_consolidation(
            (source("e1"), source("e2", predicate_key="hobby", memory_key="user:hobby")),
            user_id="u1",
            content="x",
            version_id="c1",
        )


@pytest.mark.parametrize("content", ["", "   ", "\n\t"])
def test_plan_requires_consolidated_content(content: str) -> None:
    with pytest.raises(ConsolidationError, match="nonblank_consolidated_content_required"):
        plan_consolidation((source(),), user_id="u1", content=content, version_id="c1")


@pytest.mark.parametrize("user_id", ["", "  "])
def test_plan_requires_a_user(user_id: str) -> None:
    with pytest.raises(ConsolidationError, match="nonblank_user_id_required"):
        plan_consolidation((source(),), user_id=user_id, content="x", version_id="c1")


def test_plan_requires_at_least_one_source() -> None:
    with pytest.raises(ConsolidationError, match="consolidation_requires_source_versions"):
        plan_consolidation((), user_id="u1", content="x", version_id="c1")


def test_plan_rejects_episodic_layer_for_derived_rows() -> None:
    with pytest.raises(ConsolidationError, match="consolidation_layer_must_be_semantic"):
        plan_consolidation(
            (source(),), user_id="u1", content="x", version_id="c1", layer=cast("ConsolidationLayer", "episodic")
        )


def test_plan_rejects_unknown_derivation_type() -> None:
    with pytest.raises(ConsolidationError, match="consolidation_derivation_type_invalid"):
        plan_consolidation((source(),), user_id="u1", content="x", version_id="c1", derivation_type="corroboration")


def test_plan_accepts_a_summary_derivation_type() -> None:
    plan = plan_consolidation((source(),), user_id="u1", content="x", version_id="c1", derivation_type="summary")
    assert plan.derivation_type == "summary"


def test_plan_requires_a_boolean_single_valued_flag() -> None:
    with pytest.raises(ConsolidationError, match="single_valued_flag_required"):
        plan_consolidation((source(),), user_id="u1", content="x", version_id="c1", single_valued="yes")  # type: ignore[arg-type]


def test_plan_to_candidate_emits_derivation_links_and_no_evidence() -> None:
    """The write input must satisfy Invariant 1 through derivation, not evidence."""
    plan = plan_consolidation((source("e1"), source("e2")), user_id="u1", content="x", version_id="c1")
    candidate = plan_to_candidate(plan)
    assert candidate.evidence == ()
    assert candidate.derivations[0].derivation_type == "consolidation"
    assert tuple(item.source_version_id for item in candidate.derivations) == ("e1", "e2")
    assert candidate.version_id == "c1"
    assert candidate.single_valued is False
    # ``require_provenance`` is the Invariant 1/8 precondition the commit path enforces.
    candidate.require_provenance()
