"""Synthetic local adapter, not a baseline or an implementation of the engine."""

from datetime import datetime
from time import perf_counter

from evaluation.harness.config import HarnessConfig
from evaluation.harness.models import (
    AnswerResult,
    DeletionReceipt,
    DeletionScope,
    IngestReceipt,
    MetricScore,
    QueryMode,
    QuestionRecord,
    QueueDrainStats,
    RetrievalResult,
    RetrievedItem,
    ScoreResult,
    TurnRecord,
    Usage,
)


class DummyAdapter:
    """Echo the latest user turn to exercise replay without external calls or gold."""

    def __init__(self, config: HarnessConfig) -> None:
        if config.system_id != "fixture-dummy" or config.adapter_version != "fixture-v1":
            raise ValueError("dummy_requires_its_explicit_fixture_identity")
        self.config = config
        self.turns: dict[str, list[TurnRecord]] = {}
        self.clock: datetime | None = None

    def reset_user(self, user_id: str) -> None:
        self.turns[user_id] = []

    def set_clock(self, ts: datetime) -> None:
        self.clock = ts

    def ingest_turn(self, turn: TurnRecord) -> IngestReceipt:
        self.turns[turn.user_id].append(turn)
        return IngestReceipt(turn_id=turn.turn_id, usage=Usage.zero())

    def drain_queue(self, user_id: str) -> QueueDrainStats:
        return QueueDrainStats(processed=0, pending=0, usage=Usage.zero())

    def retrieve(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> RetrievalResult:
        started = perf_counter()
        turns = [turn for turn in self.turns[user_id] if turn.role == "user"]
        if not turns:
            return RetrievalResult(
                context_tokens=0, retrieval_latency_ms=(perf_counter() - started) * 1000, usage=Usage.zero()
            )
        turn = turns[-1]
        # One Unicode code point per synthetic token is explicit and provider-free.
        content = turn.content[:token_budget]
        return RetrievalResult(
            items=(RetrievedItem(reference_id=turn.turn_id, content=content, evidence_turn_ids=(turn.turn_id,)),),
            rendered_context=content,
            context_tokens=len(content),
            usage=Usage.zero(),
            retrieval_latency_ms=(perf_counter() - started) * 1000,
        )

    def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult:
        retrieval = self.retrieve(user_id, question.question, question.mode, as_of, self.config.B_mem)
        return AnswerResult(answer=retrieval.rendered_context, usage=Usage.zero())

    def delete(self, user_id: str, scope: DeletionScope, target_ref: str) -> DeletionReceipt:
        return DeletionReceipt(supported=False, usage=Usage.zero())


def dummy_score(question: QuestionRecord, retrieval: RetrievalResult, answer: AnswerResult) -> ScoreResult:
    """A software echo check, deliberately unrelated to H1–H4 metric scores."""
    return ScoreResult(
        metrics=(MetricScore(name="fixture_echo", value=float(answer.answer == retrieval.rendered_context)),)
    )
