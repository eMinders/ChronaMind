"""Language-independent single-valued slot classification for the app's fast-lane writes (``V2-D115``, ``V2-D121``).

A user message becomes an engine claim through ``assert``, and the engine only supersedes a claim that is
``cardinality="single"`` *and* keyed by a ``semantic_slot``. So the app may only call a message single-valued when it
can also name the slot, and it must be right when it does: a false "single" silently retires a true belief, while a
false "multi" only leaves an old belief standing. That asymmetry decides the whole design.

**Semantic understanding, then language-independent checks.** The understanding is done by a language model through
structured output (:class:`SlotExtraction`): it reads the message *in whatever language or mix of languages it is
written* and reports which slot it states, whether it states the speaker's *current* value, and the value as written.
No keyword, verb list or grammar for any language lives in this module. The model's answer is then accepted only if
every check in :func:`decide` passes, and none of those checks depends on a language either:

* the slot is in the closed vocabulary (:data:`SLOT_VOCABULARY`);
* the statement is about the speaker's *current* state: a past fact ("I lived in Berlin in 2010"), a plan, a
  hypothetical, a negation or a question is never single-valued;
* the value is **grounded**: it occurs verbatim in the message after Unicode normalization (NFKC, case folding,
  combining marks removed), so a translated, paraphrased or invented value is refused;
* the model's own confidence clears :data:`MIN_CONFIDENCE`.

**Failure is the safe side.** No model configured, a timeout, a refusal, an invalid answer or any exception gives
``("multi", None)``: the claim is still recorded, it just does not supersede anything.

The deterministic decision rules are pinned by ``tests/test_memory_slots.py``; the model's understanding of Arabic,
English, mixed-language messages, corrections and historical facts is measured against a real model by
``tests/live/test_memory_slots_live.py``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal, NamedTuple, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.services.llm import llm_service

type Cardinality = Literal["single", "multi"]

#: Where the user lives now.
RESIDENCE = "residence"
#: Who the user works for now.
EMPLOYER = "employer"
#: What the user is called.
NAME = "name"

#: Every slot this module can emit. The engine normalizes slots (``semantic.slots.normalize_slot``); these are already
#: in that canonical form, so the spelling sent is the spelling stored.
SLOT_VOCABULARY: frozenset[str] = frozenset({RESIDENCE, EMPLOYER, NAME})

#: The lowest self-reported confidence accepted. A soft extra gate: the hard ones are tense, subject and grounding.
MIN_CONFIDENCE = 0.8
#: How many preceding messages are shown as context (so "No, Paris." after "Do you still live in Berlin?" is readable).
CONTEXT_MESSAGES = 4
#: The longest message classified. A long message is narrative, not a slot update, and is never single-valued.
MAX_MESSAGE_CHARS = 1000


class SlotClassification(NamedTuple):
    """``("single", slot)`` for a clear single-valued state statement, otherwise ``("multi", None)``."""

    cardinality: Cardinality
    semantic_slot: str | None


MULTI = SlotClassification("multi", None)


class SlotExtraction(BaseModel):
    """What the model reports about one message. Every field is checked by :func:`decide` before it is trusted."""

    slot: Literal["residence", "employer", "name", "none"] = Field(
        description="Which personal attribute of the speaker the message states, or 'none'."
    )
    temporal_status: Literal["current", "past", "future", "hypothetical", "negated", "question", "other"] = Field(
        description=(
            "'current' only if the message asserts the speaker's value as true now (including a correction or a "
            "completed move). 'past' for a former value, 'future' for a plan, 'negated' for a denial without a new "
            "value, 'question' for a question, 'hypothetical' for a wish or condition, 'other' otherwise."
        )
    )
    subject_is_speaker: bool = Field(description="True only if the attribute belongs to the speaker themself.")
    value: str = Field(
        description=(
            "The value exactly as written in the message, copied character for character in its original script and "
            "language (never translated or normalized). Empty when slot is 'none'."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0, description="How certain you are, from 0 to 1.")


SYSTEM_PROMPT = """You label one chat message from a user to an assistant.
The message may be written in any language, in any script, or in a mix of languages. Understand its meaning; do not
rely on particular words.

Decide whether the message states one of these attributes of the SPEAKER, and whether it is their CURRENT value:
- residence: the city, region or country where the speaker lives now
- employer: the organisation the speaker works for now
- name: the speaker's own name

Rules:
- slot is 'none' if the message states none of them, states several values for one attribute, or is about someone
  else (then subject_is_speaker is false).
- temporal_status is 'current' only when the message asserts the value as true now. A correction ("no, I meant Paris")
  or a completed move ("I moved to Paris") is current. A former value ("I lived in Berlin in 2010"), a plan
  ("I will move to Rome"), a wish, a condition, a denial without a new value, and a question are not current.
- value is copied exactly from the message, in its original language and script. Never translate it.
Earlier messages are context only: the value must come from the message being labelled."""


def _comparable(text: str) -> str:
    """NFKC, case-folded, combining marks removed and whitespace collapsed: one spelling per string, in any script."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text).casefold())
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(unicodedata.normalize("NFC", stripped).split())


def _strip_edges(value: str) -> str:
    """Drop surrounding punctuation and symbols (any script) that a model may carry over from the sentence."""
    start, end = 0, len(value)
    while start < end and unicodedata.category(value[start])[0] in "PSZ":
        start += 1
    while end > start and unicodedata.category(value[end - 1])[0] in "PSZ":
        end -= 1
    return value[start:end]


def is_grounded(value: str, message: str) -> bool:
    """Whether ``value`` occurs verbatim in ``message`` once both are normalized the same way."""
    needle = _comparable(_strip_edges(value))
    return bool(needle) and needle in _comparable(message)


def decide(message: str, extraction: SlotExtraction) -> SlotClassification:
    """Accept the model's extraction as a single-valued slot update only when every language-independent check passes."""
    if extraction.slot not in SLOT_VOCABULARY:
        return MULTI
    if extraction.temporal_status != "current" or not extraction.subject_is_speaker:
        return MULTI
    if extraction.confidence < MIN_CONFIDENCE:
        return MULTI
    if not is_grounded(extraction.value, message):
        return MULTI
    return SlotClassification("single", extraction.slot)


class SlotClassifier(Protocol):
    """Classifies the newest user message, with the messages before it as context."""

    async def classify(self, message: str, context: Sequence[str]) -> SlotClassification:
        """``("single", slot)`` or ``("multi", None)``; never raises."""
        ...


class NullSlotClassifier:
    """No semantic classifier configured: every message is multi-valued (the safe side)."""

    async def classify(self, message: str, context: Sequence[str]) -> SlotClassification:
        """Always ``("multi", None)``."""
        del message, context
        return MULTI


#: ``(messages, config) -> SlotExtraction``: one structured-output model call. Injected so a test can drive the
#: decision path with a recorded answer and the live evaluation with any OpenAI-compatible endpoint.
type Extract = Callable[[list[BaseMessage], RunnableConfig], Awaitable[SlotExtraction]]


def build_messages(message: str, context: Sequence[str]) -> list[BaseMessage]:
    """The model input: the instructions, then the recent context, then the message being labelled."""
    recent = [line for line in context if line.strip()][-CONTEXT_MESSAGES:]
    context_block = "\n".join(f"- {line[:MAX_MESSAGE_CHARS]}" for line in recent) or "(none)"
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Earlier messages (context only):\n{context_block}\n\nMessage to label:\n{message}"),
    ]


class LLMSlotClassifier:
    """The semantic classifier: one structured-output model call, then :func:`decide`. Fails closed to ``MULTI``."""

    def __init__(self, extract: Extract) -> None:
        """``extract`` performs the model call; see :data:`Extract`."""
        self._extract = extract

    async def classify(self, message: str, context: Sequence[str]) -> SlotClassification:
        """Classify ``message``. Any failure, of the model or of a check, is ``("multi", None)``."""
        if not message.strip() or len(message) > MAX_MESSAGE_CHARS:
            return MULTI
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        config: RunnableConfig = {"callbacks": callbacks, "run_name": "memory_slot_classification"}
        try:
            extraction = await self._extract(build_messages(message, context), config)
        except Exception:  # noqa: BLE001 - a failed classification must never lose the claim; multi is the safe side
            logger.exception("memory_slot_classification_failed")
            return MULTI
        classification = decide(message, extraction)
        # content-free: the slot and the outcome, never the message or the value
        logger.info(
            "memory_slot_classified",
            proposed_slot=extraction.slot,
            temporal_status=extraction.temporal_status,
            cardinality=classification.cardinality,
        )
        return classification


async def llm_service_extract(messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
    """The production :data:`Extract`: the app's LLM service (retries, fallback) with structured output."""
    return await llm_service.call(
        messages,
        model_name=settings.MEMORY_SLOT_CLASSIFIER_MODEL or None,
        response_format=SlotExtraction,
        config=config,
        temperature=0,
    )


def configured_classifier() -> SlotClassifier:
    """The classifier the settings select: the semantic one unless ``MEMORY_SLOT_CLASSIFIER=off``."""
    if settings.MEMORY_SLOT_CLASSIFIER == "off":
        return NullSlotClassifier()
    return LLMSlotClassifier(llm_service_extract)
