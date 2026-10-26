"""Local adapter controls and one bounded service scope per harness operation."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import uuid4

from evaluation.baselines.context import answer_messages, history_items, join_items
from evaluation.baselines.contracts import BaselineSettings, Message, ModelServices, OperationScope
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.config import HarnessConfig
from evaluation.harness.models import (
    AnswerResult,
    DeletionReceipt,
    DeletionScope,
    IngestReceipt,
    QueryMode,
    QuestionRecord,
    QueueDrainStats,
    RetrievalDiagnostics,
    RetrievalResult,
    TurnRecord,
    Usage,
)

type OperationName = Literal["ingest_turn", "drain_queue", "retrieve", "answer", "delete"]


@dataclass
class UserState:
    turns: list[TurnRecord] = field(default_factory=list)
    generation: str = field(default_factory=lambda: uuid4().hex)
    mutation: int = 0


@dataclass(frozen=True)
class PreparedContext:
    question: QuestionRecord
    reference_clock: datetime
    generation: str
    mutation: int
    retrieval: RetrievalResult


class BaseAdapter:
    """No provider/store work is permitted in constructors or local hooks."""

    def __init__(self, config: HarnessConfig, settings: BaselineSettings, services: ModelServices) -> None:
        settings = BaselineSettings.model_validate(settings.model_dump())
        self.config = settings.validate_harness(config)
        self.settings = settings
        self.services = services
        if services.settings.sha256() != settings.sha256() or services.tokenizer.identity != settings.tokenizer_id:
            raise ValueError("baseline_model_services_configuration_mismatch")
        self.clock: datetime | None = None
        self._users: dict[str, UserState] = {}
        self._questions: dict[str, QuestionRecord] = {}
        self._prepared: dict[str, PreparedContext] = {}
        self._events: list[dict[str, object]] = []
        self._history_queries: dict[tuple[str, str, str], RetrievalDiagnostics] = {}
        self.factory_provenance: dict[str, object] = {}

    def _state(self, user_id: str) -> UserState:
        if not user_id.strip() or user_id not in self._users:
            raise ValueError("baseline_user_not_initialized")
        return self._users[user_id]

    def _invalidate(self, user_id: str) -> None:
        self._prepared.pop(user_id, None)

    def _run[T](self, name: OperationName, operation: Callable[[], T]) -> tuple[T, Usage]:
        active: OperationScope | None = None
        try:
            with self.services.scope(getattr(self.config.operation_limits, name)) as active:
                result = operation()
            return result, active.usage
        except Exception as error:
            # Runtime scopes contain aggregate attempted usage, including the
            # failed call's conservative bound; never add the exception again.
            usage = (
                active.usage
                if active is not None
                else (error.usage if isinstance(error, AccountedOperationError) else Usage.zero())
            )
            self._emit(
                {"event": "baseline_operation_failed", "operation": name, "exception_type": type(error).__name__}
            )
            raise AccountedOperationError("baseline_operation_failed", usage) from None

    def reset_user(self, user_id: str) -> None:
        if not user_id.strip():
            raise ValueError("nonblank_baseline_user_required")
        self._users[user_id] = UserState()
        self._questions.pop(user_id, None)
        self._invalidate(user_id)

    def set_clock(self, ts: datetime) -> None:
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise ValueError("aware_baseline_clock_required")
        self.clock = ts
        self._prepared.clear()
        self._questions.clear()

    def prepare_question(self, question: QuestionRecord) -> None:
        self._state(question.user_id)
        if self.clock is None or question.stratum is not None:
            raise ValueError("clean_question_and_replay_clock_required")
        self._questions[question.user_id] = question
        self._invalidate(question.user_id)

    def _question(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> QuestionRecord:
        self._state(user_id)
        question = self._questions.get(user_id)
        if (
            question is None
            or self.clock is None
            or as_of.tzinfo is None
            or as_of.utcoffset() is None
            or question.question != query
            or question.mode != mode
            or as_of != (question.as_of_time or self.clock)
            or token_budget != self.config.B_mem
        ):
            raise ValueError("prepared_question_retrieval_mismatch")
        return question

    def _remember(self, question: QuestionRecord, as_of: datetime, retrieval: RetrievalResult) -> RetrievalResult:
        state = self._state(question.user_id)
        if self.clock is None:
            raise ValueError("prepared_reference_clock_required")
        self._prepared[question.user_id] = PreparedContext(
            question, self.clock, state.generation, state.mutation, retrieval
        )
        return retrieval

    def _answer_messages(self, question: QuestionRecord, rendered_context: str) -> tuple[Message, ...]:
        if self.clock is None:
            raise ValueError("answer_reference_clock_required")
        return answer_messages(self.settings, question, rendered_context, self.clock)

    def _validate_turn(self, turn: TurnRecord) -> UserState:
        state = self._state(turn.user_id)
        if self.clock is None or turn.occurred_at > self.clock:
            raise ValueError("turn_exceeds_replay_clock")
        if any(previous.turn_id == turn.turn_id for previous in state.turns):
            raise ValueError("duplicate_baseline_turn")
        return state

    def ingest_turn(self, turn: TurnRecord) -> IngestReceipt:
        state = self._validate_turn(turn)

        def ingest() -> None:
            state.turns.append(turn)
            state.mutation += 1
            self._invalidate(turn.user_id)

        _, usage = self._run("ingest_turn", ingest)
        return IngestReceipt(turn_id=turn.turn_id, usage=usage)

    def drain_queue(self, user_id: str) -> QueueDrainStats:
        self._state(user_id)
        _, usage = self._run("drain_queue", lambda: None)
        return QueueDrainStats(processed=0, pending=0, usage=usage)

    def _history_retrieval(self, question: QuestionRecord, turns: list[TurnRecord]) -> RetrievalResult:
        if self.clock is None:
            raise ValueError("answer_reference_clock_required")
        items, dropped_tokens = history_items(turns, question, self.settings, self.services.tokenizer, self.clock)
        context = join_items(items)
        dropped_items = len(turns) - len(items)
        diagnostics = RetrievalDiagnostics(
            context_policy=self.config.context_policy,
            source_items=len(turns),
            selected_items=len(items),
            truncated_items=dropped_items,
            truncated_tokens=dropped_tokens,
            candidate_count=len(turns),
        )
        key = (self._state(question.user_id).generation, question.user_id, question.question_id)
        self._history_queries[key] = diagnostics
        if dropped_items:
            self._emit(
                {
                    "event": "history_truncated",
                    "user_id": question.user_id,
                    "question_id": question.question_id,
                    **diagnostics.model_dump(mode="json"),
                }
            )
        return RetrievalResult(
            items=items,
            rendered_context=context,
            context_tokens=len(self.services.tokenizer.encode(context)),
            diagnostics=diagnostics,
            usage=Usage.zero(),
        )

    def retrieve(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> RetrievalResult:
        raise NotImplementedError

    def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult:
        state = self._state(user_id)
        prepared = self._prepared.get(user_id)
        if (
            prepared is None
            or prepared.question != question
            or question.user_id != user_id
            or question.stratum is not None
            or prepared.reference_clock != as_of
            or self.clock != as_of
            or prepared.generation != state.generation
            or prepared.mutation != state.mutation
        ):
            raise ValueError("prepared_answer_context_mismatch")
        messages = self._answer_messages(question, prepared.retrieval.rendered_context)

        def complete() -> str:
            if (
                self.services.tokenizer.count_messages(messages)
                + self.settings.request_overhead_tokens
                + self.settings.max_output_tokens
            ) > (self.settings.context_window_tokens):
                raise ValueError("answer_context_window_exceeded")
            return self.services.complete(messages).text

        text, usage = self._run("answer", complete)
        return AnswerResult(answer=text, usage=usage)

    def delete(self, user_id: str, scope: DeletionScope, target_ref: str) -> DeletionReceipt:
        state = self._state(user_id)
        if not target_ref.strip():
            raise ValueError("nonblank_baseline_deletion_target_required")

        def remove() -> int | None:
            if scope == "topic":
                return None
            previous = len(state.turns)
            state.turns = [] if scope == "all" else [turn for turn in state.turns if turn.turn_id != target_ref]
            state.mutation += 1
            self._invalidate(user_id)
            return previous - len(state.turns)

        count, usage = self._run("delete", remove)
        return DeletionReceipt(supported=count is not None, deleted_count=count, usage=usage)

    def _emit(self, event: dict[str, object]) -> None:
        self._events.append(event)

    def drain_adapter_events(self) -> tuple[dict[str, object], ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def adapter_metadata(self) -> dict[str, object]:
        return {
            **({"factory": self.factory_provenance} if self.factory_provenance else {}),
            "adapter_version": self.config.adapter_version,
            "system_id": self.config.system_id,
            "settings_sha256": self.settings.sha256(),
            "context_policy": self.config.context_policy,
            "source_unit": "original_turn",
            "speaker_policy": "preserve_source_speaker",
            "deletion": {"memory": "local_raw_turn", "all": "local_raw_history", "topic": "not_supported"},
            "deletion_boundary": "current_replay_generation_raw_state_only",
            "reset_policy": "new_local_replay_generation",
            "history_truncation": {
                "question_cases": len(self._history_queries),
                "truncated_question_cases": sum(item.truncated_items > 0 for item in self._history_queries.values()),
                "truncated_items": sum(item.truncated_items for item in self._history_queries.values()),
                "truncated_tokens": sum(item.truncated_tokens for item in self._history_queries.values()),
            },
        }
