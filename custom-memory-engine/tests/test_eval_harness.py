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
