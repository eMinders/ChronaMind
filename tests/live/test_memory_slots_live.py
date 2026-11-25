"""The semantic slot classifier against a real language model (``V2-D121``): Arabic, English and mixed-language text.

Opt-in, because it needs a model: ``MEMORY_SLOT_LIVE_EVAL=1`` plus a configured provider (for an OpenAI-compatible
gateway, ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL``; ``MEMORY_SLOT_CLASSIFIER_MODEL`` picks the model). It drives the
production path, :func:`~app.services.memory_slots.llm_service_extract` through the app's LLM service, then the app's
own checks. Run::

    export MEMORY_SLOT_LIVE_EVAL=1 MEMORY_SLOT_CLASSIFIER=llm OPENAI_API_KEY=... OPENAI_BASE_URL=...
    uv run pytest -s tests/live/test_memory_slots_live.py

Every negative case must stay ``multi``: a false "single" retires a true belief, so precision is the hard requirement.
Every positive case is asserted too; a miss there is the safe side, but a model that misses these is not good enough.
"""

from __future__ import annotations

import os

import pytest

from app.services.memory_slots import (
    EMPLOYER,
    MULTI,
    NAME,
    RESIDENCE,
    LLMSlotClassifier,
    SlotClassification,
    llm_service_extract,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("MEMORY_SLOT_LIVE_EVAL") != "1",
        reason="live model evaluation: set MEMORY_SLOT_LIVE_EVAL=1 and a provider",
    ),
]


@pytest.fixture
def anyio_backend() -> str:
    """Run on asyncio."""
    return "asyncio"


def single(slot: str) -> SlotClassification:
    return SlotClassification("single", slot)


#: (context, message, expected). Context is the earlier messages of the conversation, oldest first.
CASES: list[tuple[tuple[str, ...], str, SlotClassification]] = [
    # English: current values
    ((), "I live in Berlin", single(RESIDENCE)),
    ((), "I moved to Paris last month", single(RESIDENCE)),
    ((), "I work at Siemens", single(EMPLOYER)),
    ((), "My name is Omar", single(NAME)),
    # Arabic: current values
    ((), "أنا ساكن في برلين", single(RESIDENCE)),
    ((), "انتقلت إلى باريس الشهر الماضي", single(RESIDENCE)),
    ((), "أعمل في شركة أرامكو", single(EMPLOYER)),
    ((), "اسمي عمر", single(NAME)),
    # mixed-language messages
    ((), "أنا دلوقتي ساكن في Berlin", single(RESIDENCE)),
    ((), "I just started working at شركة المراعي", single(EMPLOYER)),
    ((), "Btw اسمي Omar", single(NAME)),
    # corrections, with the conversation that makes them readable
    (("I live in Berlin",), "No wait, sorry, I meant Paris, not Berlin", single(RESIDENCE)),
    (("أنا ساكن في برلين",), "لا، قصدي باريس مش برلين", single(RESIDENCE)),
    (("Where do you live now?",), "Paris", single(RESIDENCE)),
    # Berlin -> Paris -> Berlin: each statement is the current value when it is said
    ((), "I live in Berlin", single(RESIDENCE)),
    (("I live in Berlin",), "I moved to Paris", single(RESIDENCE)),
    (("I live in Berlin", "I moved to Paris"), "I moved back to Berlin", single(RESIDENCE)),
    (("أنا ساكن في برلين", "انتقلت إلى باريس"), "رجعت أعيش في برلين", single(RESIDENCE)),
    # historical facts are not the current value
    ((), "I lived in Berlin in 2010", MULTI),
    ((), "كنت أعيش في برلين سنة 2010", MULTI),
    ((), "كنت شغال في Google زمان", MULTI),
    ((), "Back in college my name was Bobby", MULTI),
    # plans, wishes, conditions, denials, questions
    ((), "I'm thinking about moving to Rome next year", MULTI),
    ((), "أفكر أنتقل لروما السنة الجاية", MULTI),
    ((), "If I lived in Tokyo I would be happier", MULTI),
    ((), "I don't live in Berlin anymore", MULTI),
    ((), "Do you remember where I live?", MULTI),
    ((), "هل تتذكر أين أسكن؟", MULTI),
    # someone else's attributes, and statements that name no slot
    ((), "My brother lives in Cairo", MULTI),
    ((), "أخي يعيش في القاهرة", MULTI),
    ((), "My boss's name is Karim", MULTI),
    ((), "I love Paris in the spring", MULTI),
    ((), "عندي اجتماع في برلين بكرة", MULTI),
    ((), "I visited Berlin and Paris last year", MULTI),
]


async def test_the_real_model_classifies_every_case_as_expected() -> None:
    """One event loop for every case: the LLM service caches its HTTP client, which is bound to the loop it ran on."""
    classifier = LLMSlotClassifier(llm_service_extract)
    wrong: list[tuple[int, str, SlotClassification, SlotClassification]] = []
    for index, (context, message, expected) in enumerate(CASES):
        got = await classifier.classify(message, list(context))
        if got != expected:
            wrong.append((index, message, expected, got))
    false_single = [case for case in wrong if case[2] == MULTI]
    print(f"live slot evaluation: {len(CASES) - len(wrong)}/{len(CASES)} correct, {len(false_single)} false single")
    assert not false_single, f"precision failure (a true belief would be retired): {false_single}"
    assert not wrong, f"missed single-valued statements: {wrong}"
