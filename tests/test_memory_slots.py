"""``app/services/memory_slots.py``: the language-independent decision rules over a model's slot extraction (``V2-D121``).

A false "single" silently retires a true belief; a false "multi" only leaves an old one standing. So every check here is
a way the model's answer is *refused*, and each is shown in Arabic, English and mixed-language text: none of the checks
may depend on a language. The model's own understanding is measured separately, against a real model
(``tests/live/test_memory_slots_live.py``).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables.config import RunnableConfig

from app.services.memory_slots import (
    EMPLOYER,
    MULTI,
    NAME,
    RESIDENCE,
    SLOT_VOCABULARY,
    LLMSlotClassifier,
    NullSlotClassifier,
    SlotClassification,
    SlotExtraction,
    build_messages,
    configured_classifier,
    decide,
    is_grounded,
)
from tests.slot_fakes import current, recorded_classifier

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests on asyncio."""
    return "asyncio"


def extraction(**fields: object) -> SlotExtraction:
    """A current residence statement, with ``fields`` overridden."""
    base: dict[str, object] = {
        "slot": RESIDENCE,
        "temporal_status": "current",
        "subject_is_speaker": True,
        "value": "Paris",
        "confidence": 0.95,
    }
    return SlotExtraction.model_validate(base | fields)


ACCEPTED = [
    ("I moved to Paris", extraction(value="Paris"), RESIDENCE),
    ("انتقلت إلى باريس", extraction(value="باريس"), RESIDENCE),
    ("أنا ساكن في Berlin دلوقتي", extraction(value="Berlin"), RESIDENCE),
    ("I work at شركة المراعي now", extraction(slot=EMPLOYER, value="شركة المراعي"), EMPLOYER),
    ("اسمي أحمد", extraction(slot=NAME, value="أحمد"), NAME),
    ("My name is José", extraction(slot=NAME, value="José"), NAME),
    # a correction is a current statement; the value is the corrected one
    ("No sorry, I meant Paris, not Berlin.", extraction(value="Paris"), RESIDENCE),
    ("لا، قصدي باريس مش برلين", extraction(value="باريس"), RESIDENCE),
]


@pytest.mark.parametrize(("message", "answer", "slot"), ACCEPTED)
def test_a_grounded_current_statement_of_the_speakers_slot_is_single_valued_in_any_language(
    message: str, answer: SlotExtraction, slot: str
) -> None:
    assert decide(message, answer) == SlotClassification("single", slot)


REFUSED = [
    # a former value is history, not the current state
    ("I lived in Berlin in 2010", extraction(temporal_status="past", value="Berlin")),
    ("كنت ساكن في برلين سنة ٢٠١٠", extraction(temporal_status="past", value="برلين")),
    # plans, wishes, denials and questions
    ("Next year I will move to Rome", extraction(temporal_status="future", value="Rome")),
    ("لو كنت أعيش في روما", extraction(temporal_status="hypothetical", value="روما")),
    ("I don't live in Berlin anymore", extraction(temporal_status="negated", value="Berlin")),
    ("هل تعرف أين أسكن؟", extraction(temporal_status="question", value="")),
    # someone else's attribute
    ("My sister lives in Cairo", extraction(subject_is_speaker=False, value="Cairo")),
    ("أختي ساكنة في القاهرة", extraction(subject_is_speaker=False, value="القاهرة")),
    # a value the message never says: translated, transliterated or invented
    ("انتقلت إلى باريس", extraction(value="Paris")),
    ("I moved to Paris", extraction(value="باريس")),
    ("I moved to the city of light", extraction(value="Paris")),
    # no slot, or not confident
    ("I love Paris in spring", extraction(slot="none", value="")),
    ("I think I might be in Paris", extraction(value="Paris", confidence=0.5)),
]


@pytest.mark.parametrize(("message", "answer"), REFUSED)
def test_a_past_planned_negated_foreign_ungrounded_or_unsure_answer_is_multi(
    message: str, answer: SlotExtraction
) -> None:
    assert decide(message, answer) == MULTI == SlotClassification("multi", None)


def test_grounding_is_unicode_normalized_not_language_specific() -> None:
    # case, compatibility forms, Arabic diacritics (tashkeel) and edge punctuation do not change the value
    assert is_grounded("berlin", "I live in BERLIN.")
    assert is_grounded("باريس", "انتقلتُ إلى بَارِيس")
    assert is_grounded("«Paris»", "I moved to Paris")
    assert is_grounded("ＴＯＫＹＯ", "I live in Tokyo")  # full-width compatibility characters (NFKC)
    assert not is_grounded("", "I live in Berlin")
    assert not is_grounded("...", "I live in Berlin")
    assert not is_grounded("Bern", "I live in Belgrade")


def test_the_slot_vocabulary_is_closed() -> None:
    assert SLOT_VOCABULARY == {RESIDENCE, EMPLOYER, NAME}


async def test_the_classifier_asks_once_and_applies_the_checks_to_the_answer() -> None:
    classifier, model = recorded_classifier({"انتقلت إلى باريس": current(RESIDENCE, "باريس")})
    assert await classifier.classify("انتقلت إلى باريس", ["أنا ساكن في برلين"]) == ("single", RESIDENCE)
    assert model.asked == ["انتقلت إلى باريس"]


async def test_any_model_failure_is_multi_and_never_raises() -> None:
    async def failing(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
        raise RuntimeError("provider down")

    assert await LLMSlotClassifier(failing).classify("I moved to Paris", []) == MULTI


async def test_an_empty_or_overlong_message_is_multi_without_a_model_call() -> None:
    classifier, model = recorded_classifier({})
    assert await classifier.classify("   ", []) == MULTI
    assert await classifier.classify("I moved to Paris " * 100, []) == MULTI
    assert model.asked == []


async def test_the_null_classifier_is_always_multi() -> None:
    assert await NullSlotClassifier().classify("I moved to Paris", []) == MULTI


def test_the_settings_select_the_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEMORY_SLOT_CLASSIFIER", "off")
    assert isinstance(configured_classifier(), NullSlotClassifier)
    monkeypatch.setattr(settings, "MEMORY_SLOT_CLASSIFIER", "llm")
    assert isinstance(configured_classifier(), LLMSlotClassifier)


def test_the_model_sees_recent_context_but_labels_only_the_newest_message() -> None:
    messages = build_messages("No, Paris.", ["a", "b", "c", "d", "Do you still live in Berlin?", ""])
    prompt = str(messages[-1].content)
    assert prompt.endswith("Message to label:\nNo, Paris.")
    assert "- Do you still live in Berlin?" in prompt
    assert "- a\n" not in prompt  # only the last four non-blank context messages
    # the instructions name no language and contain no per-language keyword list
    assert "any language" in str(messages[0].content)
