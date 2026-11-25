"""``app/services/memory_slots.py``: the fast lane's single-valued slot classifier (``V2-D115``).

A false "single" silently retires a true belief; a false "multi" only leaves an old one standing for the slow lane. So
these tests hold precision first: every negative family below must stay ``("multi", None)``.
"""

from __future__ import annotations

import pytest

from app.services.memory_slots import (
    EMPLOYER,
    MULTI,
    NAME,
    RESIDENCE,
    SLOT_VOCABULARY,
    SlotClassification,
    classify_slot,
)

SINGLE_VALUED = [
    # residence
    ("I live in Berlin", RESIDENCE),
    ("I live in Berlin.", RESIDENCE),
    ("I moved to Paris", RESIDENCE),
    ("I moved to Paris!", RESIDENCE),
    ("I just moved to Paris", RESIDENCE),
    ("I've moved to Paris", RESIDENCE),
    ("I have finally relocated to Lisbon", RESIDENCE),
    ("I moved to Berlin last spring", RESIDENCE),
    ("I live in Paris now.", RESIDENCE),
    ("I now live in Paris", RESIDENCE),
    ("I still live in Berlin", RESIDENCE),
    ("I'm based in New York", RESIDENCE),
    ("I\u2019m based in New York", RESIDENCE),  # typographic apostrophe
    ("I am currently living in San Francisco", RESIDENCE),
    ("I reside in The Hague", RESIDENCE),
    ("I live in Rio de Janeiro", RESIDENCE),
    ("I live in St. Louis", RESIDENCE),
    ("I moved to the Bay Area", RESIDENCE),
    ("Actually, I moved to Paris.", RESIDENCE),
    ("Update: I live in Oslo", RESIDENCE),
    ("  I   live in\tBerlin  ", RESIDENCE),  # whitespace is folded
    # employer
    ("I work at Google", EMPLOYER),
    ("I work for the BBC", EMPLOYER),
    ("I now work at Siemens", EMPLOYER),
    ("I'm working for Acme Corp", EMPLOYER),
    ("I am employed by Siemens AG", EMPLOYER),
    ("I'm employed at the University of Oxford", EMPLOYER),
    ("I work at AT&T", EMPLOYER),
    # name
    ("My name is Sam", NAME),
    ("My name's Sam Taylor", NAME),
]

NOT_SINGLE_VALUED = [
    # questions
    "Do I live in Berlin?",
    "I live in Berlin?",
    "Where do I live?",
    "Do you know where I work?",
    "I live in Berlin, right?",
    # negations
    "I don't live in Berlin",
    "I do not live in Berlin",
    "I no longer live in Berlin",
    "I'm not based in London",
    "I never moved to Paris",
    "I don't work at Google anymore",
    "My name is not Sam",
    # hypotheticals, wishes and plans
    "If I lived in Paris I would be happy",
    "I would live in Paris",
    "I'd live in Paris",
    "I might move to Paris",
    "I want to live in Paris",
    "I will move to Paris",
    "I'm moving to Paris",
    "I'm thinking of moving to Paris",
    "Maybe I live in Berlin",
    "I could work at Google",
    # past narrative and past states
    "I visited Paris",
    "I lived in Berlin",
    "I used to live in Berlin",
    "I moved to Paris in 2010",
    "I moved to Paris when I was five",
    "I worked at Google",
    "I was employed by Siemens",
    # not a named value
    "I live in fear",
    "I work at home",
    "I work for myself",
    "I live in a small flat",
    "i live in berlin",  # no capitalised name: precision over recall
    # lists, compounds and extra clauses
    "I live in Berlin and Paris",
    "I live in Berlin, Germany",
    "I live in Berlin and work at Google",
    "I live in Berlin with my wife",
    "I work at Google on Mondays",
    "I work at Google and Meta",
    "I live in Berlin. Paris",
    "I live in Berlin. Where should I eat?",
    "I moved to Paris for a conference",
    # someone else, or not about the user
    "We live in Berlin",
    "My sister lives in Berlin",
    "She works at Google",
    "You live in Berlin",
    "Tell me about Berlin",
    # empty
    "",
    "   ",
]


@pytest.mark.parametrize(("message", "slot"), SINGLE_VALUED)
def test_a_clear_state_statement_is_single_valued_with_its_slot(message: str, slot: str) -> None:
    assert classify_slot(message) == SlotClassification("single", slot)


@pytest.mark.parametrize("message", NOT_SINGLE_VALUED)
def test_anything_else_is_multi_with_no_slot(message: str) -> None:
    assert classify_slot(message) == MULTI == SlotClassification("multi", None)


def test_the_slot_vocabulary_is_closed_and_already_in_the_engines_normal_form() -> None:
    """The engine folds case, edges and separators (``semantic.slots.normalize_slot``); these need no folding."""
    assert SLOT_VOCABULARY == {"residence", "employer", "name"}
    for slot in SLOT_VOCABULARY:
        assert slot == slot.strip().casefold()
        assert slot.isidentifier()
        assert 2 <= len(slot) <= 120


def test_every_positive_case_covers_a_vocabulary_slot() -> None:
    assert {slot for _, slot in SINGLE_VALUED} == SLOT_VOCABULARY


def test_the_classifier_is_deterministic() -> None:
    for message, _ in SINGLE_VALUED:
        assert classify_slot(message) == classify_slot(message)


def test_a_long_message_with_a_matching_prefix_is_multi() -> None:
    """Adversarial: the statement must be the whole message, not its first clause."""
    assert classify_slot("I live in Berlin " + "and more " * 200) == MULTI
    assert classify_slot("I live in " + "Berlin " * 50) == MULTI  # more than six value words
