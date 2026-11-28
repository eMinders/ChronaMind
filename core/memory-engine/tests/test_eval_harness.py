"""The evaluation harness's own correctness: a wrong harness reports the wrong winner.

These are not tests of a model. They are tests of the measuring instrument, because the instrument decides which
model a deployment runs, and it has already been wrong once: ``quote_grounded(quote, source)`` was called with its
arguments reversed, which made every candidate look as if it paraphrased its quotes and would have rejected every
model for a defect in the harness.
"""

from __future__ import annotations

import pytest

from custom_memory_engine.semantic.grounding import quote_grounded
from evaluation.model_selection.cases import ALL_CASES, ARABIC, ENGLISH, MIXED, overlap, schema_ok


def test_the_quote_check_takes_the_quote_first() -> None:
    """The exact call shape the harness uses, pinned so the argument order cannot be reversed again."""
    turn = "I moved to Berlin last spring, so my address changed."
    assert quote_grounded("I moved to Berlin last spring", turn)
    # and the reversed call is false, which is why the bug was invisible in a pass/fail total
    assert not quote_grounded(turn, "I moved to Berlin last spring")


def test_every_grounding_case_has_a_quote_that_really_appears_in_its_turn() -> None:
    """`must_quote` fragments are claims about the data; if one is wrong, a model is penalised for being right."""
    for case in ENGLISH + ARABIC + MIXED:
        for fragment in case.must_quote:
            assert quote_grounded(fragment, case.text), f"{case.id}: {fragment!r} is not in the turn"


def test_a_case_that_needs_no_claim_expects_none() -> None:
    """The "nothing to remember" cases are what stop a model scoring well by extracting everything."""
    assert {case.id for case in ALL_CASES if not case.must_extract} >= {"en_nothing", "ar_nothing"}


def test_the_trigram_overlap_is_script_neutral() -> None:
    """A word-based metric silently discards Arabic (short words); the trigram one does not."""
    assert overlap("انتقلت إلى القاهرة", "انتقلت إلى القاهرة") == 1.0
    assert overlap("berlin apartment", "berlin apartment") == 1.0
    assert overlap("", "berlin") == 0.0
    assert overlap("paris", "berlin apartment") < 0.2


@pytest.mark.parametrize(
    ("document", "ok"),
    [
        ({"claims": []}, True),
        ({"claims": [{"content": "x", "quote": "y"}]}, True),
        ({"claims": [{"content": "x"}]}, False),  # no quote: cannot be grounded
        ({"claims": [{"content": "", "quote": "y"}]}, False),
        ({"claims": [{"content": "x", "quote": "y", "cardinality": "sometimes"}]}, False),
        # `has space` is *not* invalid: the slot layer normalizes it to `has_space` (open vocabulary, canonical form).
        # A slot it cannot represent at all is a 200-character one.
        ({"claims": [{"content": "x", "quote": "y", "semantic_slot": "s" * 200}]}, False),
        ({"claims": [{"content": "x", "quote": "y", "semantic_slot": "has space"}]}, True),
        ({"claims": [{"content": "x", "quote": "y", "confidence": 4}]}, False),
        ({"claims": "nope"}, False),
        ([], False),
    ],
)
def test_the_schema_check_accepts_only_what_the_ingestor_can_use(document: object, ok: bool) -> None:
    assert schema_ok(document)[0] is ok


def test_every_adjudication_label_is_a_relation_a_model_may_emit() -> None:
    """The failure this catches, verbatim: the second case block used the engine's *strategy* names as expected labels.

    ``supersede`` and ``reinforce`` are what ``conflict.commit_strategy`` decides; a model is asked for
    ``duplicate|update|correction|contradiction|additive|unrelated`` and can never answer either. Three candidates were
    therefore scored wrong for correct answers until this test existed -- the same class of defect as the reversed
    ``quote_grounded`` arguments, in a different part of the instrument.
    """
    from custom_memory_engine.semantic.contracts import RELATIONS

    labels = {case.adjudication[2] for case in ALL_CASES if case.adjudication is not None}
    assert labels, "the harness has no adjudication cases"
    assert labels <= RELATIONS, labels - RELATIONS


# ------------------------------------------------------------------- scoring by the engine's write (V2-D107)


def test_adjudication_is_scored_by_the_write_the_engine_would_make() -> None:
    """A move answered "duplicate" would *reinforce* the old address. The old scorer accepted it; this one does not."""
    from evaluation.model_selection.cases import ADJUDICATION_V2, adjudication_correct

    move = next(case for case in ADJUDICATION_V2 if case.id == "adj2_move")
    assert adjudication_correct(move, "update")
    assert adjudication_correct(move, "correction"), "both supersede: the same write"
    assert not adjudication_correct(move, "duplicate"), "reinforcing keeps the stale value"
    assert not adjudication_correct(move, "contradiction")


def test_an_unanswered_or_invented_label_is_never_credited() -> None:
    """Production inserts on no answer; the scorer must not turn that into a free point on every insert case."""
    from evaluation.model_selection.cases import ADJUDICATION, adjudication_correct

    unrelated = next(case for case in ADJUDICATION if case.id == "adj_unrelated")
    assert adjudication_correct(unrelated, "unrelated")
    assert adjudication_correct(unrelated, "additive"), "both insert"
    assert not adjudication_correct(unrelated, None)
    assert not adjudication_correct(unrelated, "maybe")


def test_the_scorer_asks_the_engine_rather_than_restating_it() -> None:
    """`strategy_of` is `conflict.commit_strategy` itself, so a change to the mapping changes the scoring with it."""
    from evaluation.model_selection.cases import strategy_of

    assert strategy_of("duplicate") == "reinforce"
    assert {strategy_of("update"), strategy_of("correction")} == {"supersede"}
    assert strategy_of("contradiction") == "open_conflict"
    assert {strategy_of("additive"), strategy_of("unrelated"), strategy_of(None)} == {"insert"}


def test_the_entity_set_tests_refusal_as_much_as_matching() -> None:
    """A set where every answer is "match" would reward a model that always merges."""
    from evaluation.model_selection.cases import ENTITY

    refusals = sum(1 for case in ENTITY if case.expected is None)
    assert refusals >= len(ENTITY) // 3
    assert all(case.expected is None or 1 <= case.expected <= len(case.candidates) for case in ENTITY)
    assert len({case.id for case in ENTITY}) == len(ENTITY)


async def test_the_production_entity_adjudicator_maps_numbers_to_ids_and_rejects_anything_else() -> None:
    """The model sees `1..n`, never an id; out-of-range, boolean or malformed answers are abstentions."""
    from custom_memory_engine.integrations.openai_compatible import OpenAiCompatibleEntityAdjudicator

    class Client:
        model = "fake"

        def __init__(self, answer: object) -> None:
            self.answer = answer
            self.prompts: list[str] = []

        async def complete_json(self, messages, *, max_tokens: int = 0):
            del max_tokens
            self.prompts.append(messages[-1]["content"])
            return self.answer

    offered = [("ent_secret_1", "layla hassan"), ("ent_secret_2", "sara okafor")]
    for answer, expected in (
        ({"match": 1}, "ent_secret_1"),
        ({"match": 2}, "ent_secret_2"),
        ({"match": None}, None),
        ({"match": 3}, None),
        ({"match": 0}, None),
        ({"match": True}, None),
        ({"match": "ent_secret_1"}, None),
        (["match", 1], None),
    ):
        client = Client(answer)
        assert await OpenAiCompatibleEntityAdjudicator(client).choose("ليلى", offered) == expected, answer  # type: ignore[arg-type]
        assert "ent_secret" not in client.prompts[0], "an entity id must never enter a prompt"
