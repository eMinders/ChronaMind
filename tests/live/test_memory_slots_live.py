"""The semantic slot classifier against real language models (``V2-D121``): Arabic, English and mixed-language text.

Opt-in, because it needs a model: ``MEMORY_SLOT_LIVE_EVAL=1``. Two ways to point it at one:

* ``MEMORY_SLOT_LIVE_MODEL`` unset: the production path, :func:`~app.services.memory_slots.llm_service_extract` through
  the app's LLM service (``OPENAI_API_KEY``/``OPENAI_BASE_URL``, ``MEMORY_SLOT_CLASSIFIER_MODEL``).
* ``MEMORY_SLOT_LIVE_MODEL=<model>`` with ``CME_LLM_BASE_URL``/``CME_LLM_API_KEY``: any model on that gateway, with the
  same prompt and schema. ``claude-*`` models speak the Anthropic Messages protocol (structured output by a tool
  call); every other model speaks OpenAI chat completions (structured output by ``with_structured_output``).

Either way the answer goes through the app's own checks (:func:`~app.services.memory_slots.decide`). Run::

    export MEMORY_SLOT_LIVE_EVAL=1 MEMORY_SLOT_LIVE_MODEL=claude-haiku-5-5 CME_LLM_BASE_URL=... CME_LLM_API_KEY=...
    uv run pytest -s tests/live/test_memory_slots_live.py

Every negative case must stay ``multi``: a false "single" retires a true belief, so precision is the hard requirement.
Every positive case is asserted too; a miss there is the safe side, but a model that misses these is not good enough.
"""

from __future__ import annotations

import os

import httpx
import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.services.memory_slots import (
    EMPLOYER,
    MULTI,
    NAME,
    RESIDENCE,
    Extract,
    LLMSlotClassifier,
    SlotClassification,
    SlotExtraction,
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


def gateway_extract(model: str) -> Extract:
    """An :data:`Extract` for ``model`` on the ``CME_LLM_*`` gateway, in the protocol its family speaks."""
    base_url = os.environ["CME_LLM_BASE_URL"].rstrip("/")
    api_key = os.environ["CME_LLM_API_KEY"]
    if not model.startswith("claude-"):
        chat = ChatOpenAI(model=model, base_url=base_url, api_key=SecretStr(api_key), temperature=0)
        structured = chat.with_structured_output(SlotExtraction)

        async def openai_extract(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
            return SlotExtraction.model_validate(await structured.ainvoke(messages, config))

        return openai_extract

    tool = {"name": "label", "description": "Record the label.", "input_schema": SlotExtraction.model_json_schema()}
    # a forced tool call where the model supports it; a model that refuses one (HTTP 400) is asked with "auto" plus an
    # instruction, and an answer without the tool call is asked once more, then counted as a failure (never "multi")
    choice: dict[str, str] = {"type": "tool", "name": "label"}

    async def anthropic_extract(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
        nonlocal choice
        del config
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        for _ in range(3):
            body = {
                "model": model,
                "max_tokens": 400,
                "temperature": 0,
                "system": f"{messages[0].content}\n\nAlways answer by calling the `label` tool, and only that.",
                "messages": [{"role": "user", "content": str(message.content)} for message in messages[1:]],
                "tools": [tool],
                "tool_choice": choice,
            }
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(f"{base_url}/messages", json=body, headers=headers)
            if response.status_code == 400 and choice["type"] == "tool" and "tool_choice" in response.text:
                choice = {"type": "auto"}
                continue
            if response.is_error:
                raise RuntimeError(f"gateway_http_{response.status_code}: {response.text[:200]}")
            blocks = [item for item in response.json()["content"] if item["type"] == "tool_use"]
            if blocks:
                return SlotExtraction.model_validate(blocks[0]["input"])
        raise ValueError("model_answered_without_the_label_tool")

    return anthropic_extract


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
    model = os.environ.get("MEMORY_SLOT_LIVE_MODEL", "").strip()
    extract = gateway_extract(model) if model else llm_service_extract
    # the classifier fails closed to "multi", which would hide a broken model as a run of misses: a message whose
    # every attempt failed is recorded (a failure the classifier's own retry recovered from is not)
    failed: dict[str, str] = {}

    async def counted(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
        key = str(messages[-1].content)
        try:
            answer = await extract(messages, config)
        except Exception as error:
            failed[key] = f"{type(error).__name__}: {str(error).splitlines()[0][:160] if str(error) else ''}"
            raise
        failed.pop(key, None)
        return answer

    classifier = LLMSlotClassifier(counted)
    wrong: list[tuple[int, str, SlotClassification, SlotClassification]] = []
    for index, (context, message, expected) in enumerate(CASES):
        got = await classifier.classify(message, list(context))
        if got != expected:
            wrong.append((index, message, expected, got))
    false_single = [case for case in wrong if case[2] == MULTI]
    print(
        f"live slot evaluation ({model or 'llm_service'}): {len(CASES) - len(wrong)}/{len(CASES)} correct, "
        f"{len(false_single)} false single, {len(failed)} failed model calls"
    )
    assert not failed, f"{len(failed)} cases had every model call fail ({list(failed.values())}): unmeasured"
    assert not false_single, f"precision failure (a true belief would be retired): {false_single}"
    assert not wrong, f"missed single-valued statements: {wrong}"
