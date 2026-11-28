"""Read-only LongMemEval normalization; source timestamps are interpreted as UTC.

The source format is ``YYYY/MM/DD (Day) HH:MM``. Weekday text is descriptive,
as in RL-020; the numeric calendar date supplies the instant. Gold and replay
audits remain separate from the records passed to MemorySystemAdapter.
"""

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from math import isfinite
from pathlib import Path
from typing import Any, Literal

from custom_memory_engine.protocols import ReplaySession, ReplayTurn, reference_time, schedule_replay
from evaluation.harness.models import EvaluationCase, IngestEvent, QuestionEvent, QuestionRecord, SuiteId, TurnRecord
from research.benchmarks.common import BenchmarkSource, ClockAdjustment, GoldQuestion, PreparedCase, ReplayAudit
from research.benchmarks.longmemeval.fetch import MANIFEST_PATH, VARIANT_FILES

type Variant = Literal["oracle", "s", "m"]

SUITE_IDS: dict[Variant, SuiteId] = {
    "oracle": "LongMemEval_oracle",
    "s": "LongMemEval_S",
    "m": "LongMemEval_M",
}
QUESTION_TYPES = frozenset(
    {
        "single-session-user",
        "single-session-assistant",
        "single-session-preference",
        "multi-session",
        "knowledge-update",
        "temporal-reasoning",
    }
)
SOURCE_DATE = re.compile(r"\d{4}/\d{2}/\d{2} \((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\) \d{2}:\d{2}")


def _text(value: object, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"invalid_{field}")
    return value


def _identifier(value: object, field: str) -> str:
    result = _text(value, field)
    if any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError(f"invalid_{field}")
    return result


def _array(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"invalid_{field}")
    return value


def _timestamp(value: object, field: str) -> datetime:
    raw = _text(value, field)
    if SOURCE_DATE.fullmatch(raw) is None:
        raise ValueError(f"invalid_{field}_format")
    return datetime.strptime(f"{raw[:10]} {raw[-5:]}", "%Y/%m/%d %H:%M").replace(tzinfo=UTC)


def _answer(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise ValueError("invalid_answer")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and isfinite(value):
        return str(value)
    if isinstance(value, Decimal) and value.is_finite():
        numeric = float(value)
        if isfinite(numeric):
            return str(numeric)
    raise ValueError("invalid_answer")


def prepare_item(item: Mapping[str, Any], *, variant: Variant = "s") -> PreparedCase:
    """Normalize one isolated history with D-23/R3 clocks and turn-level gold IDs.

    Session order is stable under tied dates, and turn order within a session is
    retained. Optional per-turn ``timestamp`` values use the same declared source
    format; malformed, pre-session or decreasing timestamps fail closed. Missing
    ``has_answer`` means an unlabelled turn, not evidence inferred from its text.
    """
    if variant not in SUITE_IDS:
        raise ValueError("unknown_variant")
    if not isinstance(item, Mapping):
        raise ValueError("question_record_must_be_object")
    qid = _identifier(item.get("question_id"), "question_id")
    qtype = _text(item.get("question_type"), "question_type")
    if qtype not in QUESTION_TYPES:
        raise ValueError("unsupported_question_type")
    question_text = _text(item.get("question"), "question")
    answer = _answer(item.get("answer"))
    question_date = _timestamp(item.get("question_date"), "question_date")
    raw_ids = _array(item.get("haystack_session_ids"), "haystack_session_ids")
    raw_dates = _array(item.get("haystack_dates"), "haystack_dates")
    raw_sessions = _array(item.get("haystack_sessions"), "haystack_sessions")
    if len(raw_ids) != len(raw_dates) or len(raw_ids) != len(raw_sessions):
        raise ValueError("session_arrays_must_have_equal_lengths")
    session_ids = tuple(_identifier(sid, "session_id") for sid in raw_ids)
    answer_sids = tuple(
        _identifier(sid, "answer_session_id") for sid in _array(item.get("answer_session_ids"), "answer_session_ids")
    )
    if len(set(answer_sids)) != len(answer_sids) or not set(answer_sids).issubset(session_ids):
        raise ValueError("invalid_answer_session_ids")

    # Source IDs encode answerability (the _abs suffix). Keep them in scoring
    # provenance, not in identifiers exposed to the compared system.
    identity = f"lme:{variant}:{hashlib.sha256(qid.encode('utf-8')).hexdigest()}"
    user_id = f"{identity}:user"
    sessions: list[ReplaySession] = []
    turns: dict[str, TurnRecord] = {}
    source_session_ids: dict[str, str] = {}
    gold_ids: list[str] = []
    dates = tuple(_timestamp(value, "session_date") for value in raw_dates)
    for session_index, (sid, stamp, raw_turns) in enumerate(zip(session_ids, dates, raw_sessions, strict=True)):
        session_id = f"{identity}:s{session_index:04d}"
        source_session_ids[session_id] = sid
        scheduled_inputs: list[ReplayTurn] = []
        for turn_index, raw_turn in enumerate(_array(raw_turns, "session_turns")):
            if not isinstance(raw_turn, Mapping):
                raise ValueError("turn_record_must_be_object")
            role = raw_turn.get("role")
            if role not in ("user", "assistant"):
                raise ValueError("invalid_turn_role")
            content = _text(raw_turn.get("content"), "turn_content", empty=True)
            has_answer = raw_turn.get("has_answer", False)
            if type(has_answer) is not bool:
                raise ValueError("has_answer_must_be_boolean")
            source_time = _timestamp(raw_turn["timestamp"], "turn_timestamp") if "timestamp" in raw_turn else stamp
            turn_id = f"{session_id}:t{turn_index:04d}"
            turns[turn_id] = TurnRecord(
                user_id=user_id,
                session_id=session_id,
                turn_id=turn_id,
                role=role,
                content=content,
                occurred_at=source_time,
            )
            scheduled_inputs.append(ReplayTurn(turn_id=turn_id, source_timestamp=source_time))
            if has_answer:
                gold_ids.append(turn_id)
        sessions.append(ReplaySession(session_id=session_id, timestamp=stamp, turns=tuple(scheduled_inputs)))

    schedule = schedule_replay(sessions)
    rho = reference_time(question_date, schedule)
    question = QuestionRecord(
        question_id=identity,
        user_id=user_id,
        question=question_text,
        mode="current",
        as_of_time=rho,
        stratum="abstention" if qid.endswith("_abs") else qtype,
    )
    case = EvaluationCase(
        cluster_id=identity,
        user_ids=(user_id,),
        events=(
            *(IngestEvent(clock=turn.recorded_clock, turn=turns[turn.turn.turn_id]) for turn in schedule),
            QuestionEvent(clock=rho, question=question),
        ),
    )
    # Labels can repeat in the official S/M haystacks. Array positions identify
    # occurrences; retain every occurrence and an unambiguous sort permutation.
    replay_session_indices = tuple(sorted(range(len(sessions)), key=lambda index: sessions[index].timestamp))
    replay_session_ids = tuple(source_session_ids[sessions[index].session_id] for index in replay_session_indices)
    return PreparedCase(
        case=case,
        gold=(
            GoldQuestion(
                question_id=identity,
                cluster_id=identity,
                user_id=user_id,
                answer=answer,
                question_type=qtype,
                gold_turn_ids=tuple(gold_ids),
                primary_recall_eligible=variant == "s"
                and not qid.endswith("_abs")
                and qtype != "single-session-assistant",
                abstention=qid.endswith("_abs"),
                original_question_date=question_date,
                original_date_anomaly=bool(dates) and question_date < max(dates),
            ),
        ),
        audit=ReplayAudit(
            original_session_ids=session_ids,
            replay_session_ids=replay_session_ids,
            replay_session_indices=replay_session_indices,
            clock_adjustments=tuple(
                ClockAdjustment(
                    turn_id=turn.turn.turn_id,
                    source_time=turn.turn.source_timestamp,
                    recorded_time=turn.recorded_clock,
                )
                for turn in schedule
                if turn.recorded_clock != turn.turn.source_timestamp
            ),
        ),
    )


def _record_id(record: Mapping[str, Any]) -> str:
    return _identifier(record.get("question_id"), "question_id")


class LongMemEvalAdapter(BenchmarkSource):
    """Lazy, checksummed oracle/S/M source; construction performs no file read."""

    def __init__(self, raw_path: Path, variant: Variant = "s", manifest_path: Path = MANIFEST_PATH) -> None:
        if variant not in SUITE_IDS:
            raise ValueError("unknown_variant")
        super().__init__(
            raw_path=raw_path,
            manifest_path=manifest_path,
            filename=VARIANT_FILES[variant],
            suite_id=SUITE_IDS[variant],
            normalizer=partial(prepare_item, variant=variant),
            record_id=_record_id,
        )
