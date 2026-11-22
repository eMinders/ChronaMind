"""``semantic.conflict``: recall → adjudicate → commit, with the precedence fixed (W3.5, ``V2-D89``).

The commit stage is pure, so it is tested exhaustively here without a database: the precedence between relations, the
authority veto, and the three ways an adjudicator can be wrong (a hallucinated id, an unknown relation, a neighbour
that recall did not return) -- each of which must degrade to *insert* rather than to a write the engine cannot
justify.

The recall stage is tested against the real schema in `tests/integration/test_conflict_recall_db.py`; here it is only
checked for the contract errors it raises before any statement runs.
"""

import pytest

from custom_memory_engine.semantic.authority import ASSISTANT, THIRD_PARTY, USER
from custom_memory_engine.semantic.conflict import (
    ConflictError,
    Neighbour,
    adjudicate,
    commit_strategy,
    recall_neighbours,
)
from custom_memory_engine.semantic.contracts import AdjudicatedRelation, ExtractedClaim

SINGLE = ExtractedClaim(content="I live in Berlin", verbatim_quote="live in Berlin", cardinality="single")
MULTI = ExtractedClaim(content="I met Sara", verbatim_quote="met Sara", cardinality="event")


def neighbour(version_id: str, *, authority: int = USER) -> Neighbour:
    return Neighbour(version_id=version_id, content=f"content of {version_id}", similarity=0.9, authority=authority)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------ precedence


def test_a_contradiction_is_opened_rather_than_resolved() -> None:
    """The one relation the engine must not settle: it cannot know which side is right."""
    resolution = commit_strategy(
        SINGLE, USER, [AdjudicatedRelation(existing_id="v1", relation="contradiction")], [neighbour("v1")]
    )
    assert (resolution.strategy, resolution.relation, resolution.reason_code) == (
        "open_conflict",
        "contradiction",
        "adjudicated_contradiction",
    )


def test_a_contradiction_outranks_a_duplicate_however_the_adjudicator_ordered_them() -> None:
    resolution = commit_strategy(
        SINGLE,
        USER,
        [
            AdjudicatedRelation(existing_id="v1", relation="duplicate"),
            AdjudicatedRelation(existing_id="v2", relation="contradiction"),
        ],
        [neighbour("v1"), neighbour("v2")],
    )
    assert resolution.strategy == "open_conflict"
    assert resolution.existing_id == "v2"


@pytest.mark.parametrize(
    ("relation", "strategy", "reason"),
    [
        ("duplicate", "reinforce", "adjudicated_duplicate"),
        ("correction", "supersede", "adjudicated_correction"),
        ("update", "supersede", "adjudicated_update"),
    ],
)
def test_a_decided_relation_maps_to_exactly_one_write(relation: str, strategy: str, reason: str) -> None:
    resolution = commit_strategy(
        SINGLE,
        USER,
        [AdjudicatedRelation(existing_id="v1", relation=relation)],  # type: ignore[arg-type]
        [neighbour("v1")],
    )
    assert (resolution.strategy, resolution.reason_code) == (strategy, reason)


@pytest.mark.parametrize("relation", ["additive", "unrelated"])
def test_a_relation_that_is_not_a_decision_inserts(relation: str) -> None:
    resolution = commit_strategy(
        SINGLE,
        USER,
        [AdjudicatedRelation(existing_id="v1", relation=relation)],  # type: ignore[arg-type]
        [neighbour("v1")],
    )
    assert (resolution.strategy, resolution.existing_id) == ("insert", None)


def test_no_adjudication_at_all_inserts() -> None:
    resolution = commit_strategy(SINGLE, USER, [], [neighbour("v1")])
    assert (resolution.strategy, resolution.reason_code) == ("insert", "no_relation_established")


# ----------------------------------------------------------------------------------------------- the ladder


@pytest.mark.parametrize("relation", ["duplicate", "update", "correction", "contradiction"])
def test_a_weaker_source_cannot_act_on_a_stronger_belief(relation: str) -> None:
    """A third-party claim may inform, never instruct: the attempt is quarantined, not honoured."""
    resolution = commit_strategy(
        SINGLE,
        THIRD_PARTY,
        [AdjudicatedRelation(existing_id="v1", relation=relation)],  # type: ignore[arg-type]
        [neighbour("v1", authority=USER)],
    )
    assert (resolution.strategy, resolution.reason_code) == ("quarantine", "authority_quarantine")


def test_an_equal_rank_may_act() -> None:
    resolution = commit_strategy(
        SINGLE,
        ASSISTANT,
        [AdjudicatedRelation(existing_id="v1", relation="update")],
        [neighbour("v1", authority=ASSISTANT)],
    )
    assert resolution.strategy == "supersede"


def test_a_stronger_source_may_act_on_a_weaker_belief() -> None:
    resolution = commit_strategy(
        SINGLE,
        USER,
        [AdjudicatedRelation(existing_id="v1", relation="correction")],
        [neighbour("v1", authority=THIRD_PARTY)],
    )
    assert resolution.strategy == "supersede"


# ------------------------------------------------------------------------------------ an adjudicator can be wrong


def test_a_hallucinated_id_is_ignored_rather_than_written_to() -> None:
    """Honouring an id recall never returned would be a write to a belief the engine never showed the model."""
    resolution = commit_strategy(
        SINGLE, USER, [AdjudicatedRelation(existing_id="never-seen", relation="update")], [neighbour("v1")]
    )
    assert (resolution.strategy, resolution.existing_id) == ("insert", None)


def test_an_unknown_relation_is_refused_at_the_contract_and_ignored_by_the_commit() -> None:
    """Two layers on purpose: the contract refuses it, and the commit stage still does not act on one.

    The dataclass is the first line -- an adjudicator cannot even construct `overwrites`. The commit stage's default is
    the second, for an implementation that returns a duck-typed object instead of the dataclass: an unrecognised
    relation must never become a write the engine cannot justify.
    """
    from custom_memory_engine.semantic.contracts import ContractError

    with pytest.raises(ContractError):
        AdjudicatedRelation(existing_id="v1", relation="overwrites")  # type: ignore[arg-type]

    class Duck:
        existing_id = "v1"
        relation = "overwrites"
        rationale = ""

    assert commit_strategy(SINGLE, USER, [Duck()], [neighbour("v1")]).strategy == "insert"  # type: ignore[list-item]


def test_an_invalid_candidate_authority_is_refused_before_any_decision() -> None:
    with pytest.raises(ConflictError):
        commit_strategy(SINGLE, 7, [], [neighbour("v1")])  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------------- the adapter


class _Adjudicator:
    def __init__(self, *proposals: AdjudicatedRelation) -> None:
        self._proposals = proposals
        self.seen: tuple[tuple[str, str], ...] = ()

    async def adjudicate(  # type: ignore[no-untyped-def]
        self, claim: ExtractedClaim, existing, *, language: str | None = None
    ):
        self.seen = tuple(existing)
        return self._proposals


async def test_no_adjudicator_means_no_semantic_dedup_and_that_is_a_supported_answer() -> None:
    assert await adjudicate(SINGLE, [neighbour("v1")], None) == ()


async def test_the_adjudicator_sees_text_and_ids_but_never_a_score_or_a_preferred_order() -> None:
    """An adjudicator shown a favoured answer tends to confirm it, which is the failure an isolated verifier avoids."""
    runner = _Adjudicator(AdjudicatedRelation(existing_id="v1", relation="duplicate"))
    assert await adjudicate(SINGLE, [neighbour("v1")], runner) == runner._proposals
    assert runner.seen == (("v1", "content of v1"),)


async def test_an_empty_shortlist_does_not_call_the_model_at_all() -> None:
    runner = _Adjudicator()
    assert await adjudicate(SINGLE, [], runner) == ()
    assert runner.seen == ()


# ----------------------------------------------------------------------------------------------- recall contract


async def test_recall_refuses_a_query_it_could_not_rank_before_any_statement() -> None:
    with pytest.raises(ConflictError) as refused:
        await recall_neighbours(
            None,  # type: ignore[arg-type]
            user_id="u1",
            subject_key="self",
            scope_path="/u/u1/",
        )
    assert refused.value.code == "recall_requires_an_embedding_or_a_query_text"


async def test_recall_refuses_a_scope_that_is_not_a_path() -> None:
    with pytest.raises(ConflictError) as refused:
        await recall_neighbours(
            None,  # type: ignore[arg-type]
            user_id="u1",
            subject_key="self",
            scope_path="not-a-path",
            content="x",
        )
    assert refused.value.code == "scope_path_required"


async def test_recall_refuses_a_vector_width_no_slot_can_index() -> None:
    with pytest.raises(Exception) as refused:
        await recall_neighbours(
            None,  # type: ignore[arg-type]
            user_id="u1",
            subject_key="self",
            scope_path="/u/u1/",
            embedding=(0.1, 0.2),
            embedding_model="m",
        )
    assert "embedding_dimension" in str(refused.value) or "unsupported" in str(refused.value)
