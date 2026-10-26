"""Typed adapter inputs/results; gold labels never enter adapter input records."""

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

type SuiteId = Literal["T", "G", "D", "I", "LongMemEval_S", "LoCoMo"]
type Split = Literal["dev", "test"]
type QueryMode = Literal["current", "as_of", "what_changed", "before_change", "stable"]
type DeletionScope = Literal["memory", "topic", "all"]


class Record(BaseModel):
    """Explicit, immutable JSON contracts with finite numbers and no unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class Usage(Record):
    """Accounted usage, including all failed/retried calls within an operation."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    embedding_tokens: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0)

    @property
    def tokens(self) -> int:
        """D-01's combined input/output provider-token counter."""
        return self.input_tokens + self.output_tokens

    @classmethod
    def zero(cls) -> "Usage":
        """Use only when an operation is known to have made zero charged calls."""
        return cls(input_tokens=0, output_tokens=0, embedding_tokens=0, cost_usd=Decimal(0))

    def plus(self, other: "Usage") -> "Usage":
        """Sum exact accounting without rounding currency through floating point."""
        costs = (self.cost_usd, other.cost_usd)
        precision = max(
            28,
            max(cost.adjusted() for cost in costs) - min(int(cost.as_tuple().exponent) for cost in costs) + 3,
        )
        with localcontext() as context:
            context.prec = precision
            cost = self.cost_usd + other.cost_usd
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            embedding_tokens=self.embedding_tokens + other.embedding_tokens,
            cost_usd=cost,
        )


class TurnRecord(Record):
    """One original turn; the event clock is separately injected for replay."""

    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    role: Literal["user", "assistant"]
    content: str
    occurred_at: AwareDatetime


class QuestionRecord(Record):
    """Question and temporal intent, deliberately excluding gold answers or evidence."""

    question_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    mode: QueryMode = "current"
    as_of_time: AwareDatetime | None = None
    stratum: str | None = None
    required_capabilities: tuple[Literal["retrieval", "deletion"], ...] = ()


class IngestReceipt(Record):
    turn_id: str
    usage: Usage


class QueueDrainStats(Record):
    processed: int = Field(ge=0)
    pending: int = Field(ge=0)
    usage: Usage


class RetrievedItem(Record):
    reference_id: str = Field(min_length=1)
    content: str
    evidence_turn_ids: tuple[str, ...] = ()


class RetrievalResult(Record):
    supported: bool = True
    items: tuple[RetrievedItem, ...] = ()
    rendered_context: str = ""
    context_tokens: int = Field(ge=0)
    retrieval_latency_ms: float | None = Field(default=None, ge=0)
    usage: Usage


class AnswerResult(Record):
    supported: bool = True
    answer: str = ""
    usage: Usage


class DeletionReceipt(Record):
    supported: bool
    deleted_count: int | None = Field(default=None, ge=0)
    usage: Usage


class IngestEvent(Record):
    kind: Literal["ingest"] = "ingest"
    clock: AwareDatetime
    turn: TurnRecord


class QuestionEvent(Record):
    kind: Literal["question"] = "question"
    clock: AwareDatetime
    question: QuestionRecord


class DeleteEvent(Record):
    kind: Literal["delete"] = "delete"
    clock: AwareDatetime
    user_id: str = Field(min_length=1)
    scope: DeletionScope
    target_ref: str = Field(min_length=1)


type Event = Annotated[IngestEvent | QuestionEvent | DeleteEvent, Field(discriminator="kind")]


class EvaluationCase(Record):
    """Already-normalized scenario/history; source adapters own D-23 scheduling."""

    cluster_id: str = Field(min_length=1)
    user_ids: tuple[str, ...] = Field(min_length=1)
    events: tuple[Event, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_replay(self) -> "EvaluationCase":
        """Reject ambiguous tenants, IDs, or decreasing recorded clocks before replay."""
        if len(set(self.user_ids)) != len(self.user_ids) or any(not user.strip() for user in self.user_ids):
            raise ValueError("distinct_nonempty_users_required")
        previous: dict[str, datetime] = {}
        turn_ids: set[str] = set()
        question_ids: set[str] = set()
        for event in self.events:
            user_id = (
                event.turn.user_id
                if isinstance(event, IngestEvent)
                else (event.question.user_id if isinstance(event, QuestionEvent) else event.user_id)
            )
            if user_id not in self.user_ids:
                raise ValueError("event_user_not_declared")
            if user_id in previous and event.clock < previous[user_id]:
                raise ValueError("decreasing_replay_clock")
            if isinstance(event, IngestEvent):
                if event.clock <= previous.get(user_id, datetime.min.replace(tzinfo=event.clock.tzinfo)):
                    raise ValueError("ingestion_clock_must_strictly_increase")
                if event.clock < event.turn.occurred_at or event.turn.turn_id in turn_ids:
                    raise ValueError("invalid_turn_clock_or_duplicate_id")
                turn_ids.add(event.turn.turn_id)
            if isinstance(event, QuestionEvent):
                if event.question.question_id in question_ids:
                    raise ValueError("duplicate_question_id")
                if event.question.as_of_time is not None and event.question.as_of_time > event.clock:
                    raise ValueError("question_target_exceeds_reference_clock")
                question_ids.add(event.question.question_id)
            previous[user_id] = event.clock
        return self


class MetricScore(Record):
    """Item score from an injected scorer; null is explicitly unscored, never zero."""

    name: str = Field(min_length=1)
    value: float | None


class ScoreResult(Record):
    metrics: tuple[MetricScore, ...]


class MemorySystemAdapter(Protocol):
    """The seven synchronous methods from Technical Specification §9.1.

    Implementations bind frozen configuration at construction. reset_user/set_clock
    are local, uncharged control methods; provider work belongs in accounted methods.
    """

    def reset_user(self, user_id: str) -> None: ...
    def set_clock(self, ts: datetime) -> None: ...
    def ingest_turn(self, turn: TurnRecord) -> IngestReceipt: ...
    def drain_queue(self, user_id: str) -> QueueDrainStats: ...
    def retrieve(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> RetrievalResult: ...
    def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult: ...
    def delete(self, user_id: str, scope: DeletionScope, target_ref: str) -> DeletionReceipt: ...
