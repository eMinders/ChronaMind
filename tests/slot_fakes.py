"""Recorded model answers for the semantic slot classifier, so tests exercise :func:`decide` without a model.

A :class:`RecordedModel` answers from a table keyed by the message being labelled. Every answer still passes through
:class:`~app.services.memory_slots.LLMSlotClassifier` and :func:`~app.services.memory_slots.decide`, so a test that uses
one proves the app's own checks (tense, subject, grounding, confidence), not the table.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage
from langchain_core.runnables.config import RunnableConfig

from app.services.memory_slots import LLMSlotClassifier, SlotExtraction

NONE = SlotExtraction(slot="none", temporal_status="other", subject_is_speaker=True, value="", confidence=0.99)


def current(slot: str, value: str, confidence: float = 0.95) -> SlotExtraction:
    """The model's answer for a statement of the speaker's current ``slot`` value."""
    return SlotExtraction(
        slot=slot,  # type: ignore[arg-type]
        temporal_status="current",
        subject_is_speaker=True,
        value=value,
        confidence=confidence,
    )


class RecordedModel:
    """Answers from ``answers[message]`` (``NONE`` when absent) and records every message it was asked about."""

    def __init__(self, answers: dict[str, SlotExtraction]) -> None:
        """Answer from ``answers``."""
        self.answers = answers
        self.asked: list[str] = []

    async def __call__(self, messages: list[BaseMessage], config: RunnableConfig) -> SlotExtraction:
        """The labelled message is the last line block of the last input message."""
        del config
        labelled = str(messages[-1].content).rsplit("Message to label:\n", 1)[1]
        self.asked.append(labelled)
        return self.answers.get(labelled, NONE)


def recorded_classifier(answers: dict[str, SlotExtraction]) -> tuple[LLMSlotClassifier, RecordedModel]:
    """A real :class:`LLMSlotClassifier` over a :class:`RecordedModel`."""
    model = RecordedModel(answers)
    return LLMSlotClassifier(model), model
