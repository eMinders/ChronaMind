"""``apps/api/app/services/memory_slots.py``: the language-independent decision rules over a model's slot extraction (``V2-D121``).

A false "single" silently retires a true belief; a false "multi" only leaves an old one standing. So every check here is
a way the model's answer is *refused*, and each is shown in Arabic, English and mixed-language text: none of the checks
may depend on a language. The model's own understanding is measured separately, against a real model
(``tests/live/test_memory_slots_live.py``).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables.config import RunnableConfig

import app.services.memory_slots as memory_slots
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
    # a one-character value would match almost any message
    assert not is_grounded("i", "I live in Berlin")
    assert not is_grounded("ب", "انتقلت إلى باريس")
    # no word-boundary rule: an attached Arabic preposition still grounds the name
    assert is_grounded("برلين", "أنا ساكن ببرلين")


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


async def test_a_malformed_answer_is_asked_once_more_and_a_second_failure_is_multi() -> None:
    calls: list[int] = []

    async def flaky(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("structured output could not be parsed")
        return extraction(value="Paris")

    assert await LLMSlotClassifier(flaky).classify("I moved to Paris", []) == ("single", RESIDENCE)
    assert len(calls) == 2

    async def always_malformed(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
        calls.append(1)
        raise ValueError("structured output could not be parsed")

    calls.clear()
    assert await LLMSlotClassifier(always_malformed).classify("I moved to Paris", []) == MULTI
    assert len(calls) == 2


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
    # a model the app's registry does not serve is refused once, here, not on every turn
    monkeypatch.setattr(settings, "MEMORY_SLOT_CLASSIFIER_MODEL", "no-such-model")
    assert isinstance(configured_classifier(), NullSlotClassifier)


def test_the_model_sees_recent_context_but_labels_only_the_newest_message() -> None:
    messages = build_messages("No, Paris.", ["a", "b", "c", "d", "Do you still live in Berlin?", ""])
    prompt = str(messages[-1].content)
    assert prompt.endswith("Message to label:\nNo, Paris.")
    assert "- Do you still live in Berlin?" in prompt
    assert "- a\n" not in prompt  # only the last four non-blank context messages
    # the instructions name no language and contain no per-language keyword list
    assert "any language" in str(messages[0].content)


class _LoggerSpy:
    """Records every log call. ``exception`` is recorded separately: its traceback is what carried the content."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def __getattr__(self, level: str):  # noqa: ANN204 - info/warning/exception/... all record
        def record(event: str, *args: object, **fields: object) -> None:
            self.calls.append((level, event, {"args": args, **fields}))

        return record


async def test_a_failed_classification_logs_no_message_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validator's or parser's message quotes the model's answer, which quotes the user: none of it is logged."""
    spy = _LoggerSpy()
    monkeypatch.setattr(memory_slots, "logger", spy)

    async def leaky(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
        return SlotExtraction.model_validate({"slot": "residence", "value": "SECRET-PLACE"})  # fields missing

    assert await LLMSlotClassifier(leaky).classify("I moved to SECRET-PLACE", []) == MULTI
    assert spy.calls == [
        ("warning", "memory_slot_classification_failed", {"args": (), "error_type": "ValidationError"})
    ]
    # no traceback-carrying call, and the validation error itself does not echo the input
    with pytest.raises(ValueError) as error:
        SlotExtraction.model_validate({"slot": "residence", "value": "SECRET-PLACE"})
    assert "SECRET-PLACE" not in str(error.value)


async def test_the_classifier_never_falls_back_to_a_model_it_was_not_evaluated_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One model, no substitution: an untested model must not decide that a past fact is current."""
    import app.services.memory_slots as memory_slots

    seen: list[str | None] = []

    async def only_this_model(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
        seen.append(memory_slots.classifier_model())
        raise RuntimeError("provider_error")

    classifier = LLMSlotClassifier(only_this_model)
    assert await classifier.classify("I live in Paris", []) == MULTI
    assert seen == [memory_slots.classifier_model()]

    # and the service is asked for exactly one model: `fallback=False` is what makes the second call impossible
    captured: dict[str, object] = {}

    async def record_call(messages: list[BaseMessage], **kwargs: object) -> SlotExtraction:
        captured.update(kwargs)
        return SlotExtraction(slot="none", temporal_status="other", subject_is_speaker=False, value="", confidence=1.0)

    monkeypatch.setattr(memory_slots.llm_service, "call", record_call)
    await memory_slots.llm_service_extract([], {})
    assert captured["fallback"] is False
    assert captured["model_name"] == memory_slots.classifier_model()


def test_only_llm_enables_the_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo, `false` or `0` must disable the classifier; an unrecognized setting never switches on the risky side."""
    import app.services.memory_slots as memory_slots

    for value, expected_null in (("llm", False), ("off", True), ("false", True), ("0", True), ("lmm", True)):
        monkeypatch.setattr(memory_slots.settings, "MEMORY_SLOT_CLASSIFIER", value)
        monkeypatch.setattr(memory_slots.settings, "MEMORY_SLOT_CLASSIFIER_MODEL", "")
        monkeypatch.setattr(memory_slots, "classifier_model", lambda: "gpt-5.6-luna")
        classifier = memory_slots.configured_classifier()
        assert isinstance(classifier, NullSlotClassifier) is expected_null, value


def test_an_unknown_classifier_model_disables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.memory_slots as memory_slots

    monkeypatch.setattr(memory_slots.settings, "MEMORY_SLOT_CLASSIFIER", "llm")
    monkeypatch.setattr(memory_slots, "classifier_model", lambda: "no-such-model")
    monkeypatch.setattr(memory_slots.LLMRegistry, "get_all_names", classmethod(lambda cls: ["gpt-5.6-luna"]))
    assert isinstance(memory_slots.configured_classifier(), NullSlotClassifier)
