"""Read-only LoCoMo text replay with explicit human-speaker and evidence identity.

One conversation is one synthetic evaluation tenant; neither human is relabelled
as an assistant. Original text and speaker names survive independently. Source
session numbers define original session order; chronological replay stable-sorts
that order by session time. The documented English, timezone-free source dates
are interpreted as UTC, never as this machine's local timezone. Image URLs,
generated observations and summaries are not fetched or treated as dialogue.

Only source structure is normalized here. Official answer scoring is separate;
gold annotations never enter MemorySystemAdapter input records.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any

from custom_memory_engine.protocols import ReplaySession, ReplayTurn, reference_time, schedule_replay
from evaluation.harness.models import EvaluationCase, IngestEvent, QuestionEvent, QuestionRecord, TurnRecord
from research.benchmarks.common import BenchmarkSource, ClockAdjustment, GoldQuestion, PreparedCase, ReplayAudit
from research.benchmarks.locomo.fetch import FILENAME, MANIFEST_PATH

_SESSION = re.compile(r"session_([1-9][0-9]*)\Z")
_SESSION_DATE = re.compile(r"session_([1-9][0-9]*)_date_time\Z")
_DIALOGUE = re.compile(r"D([1-9][0-9]*):(0|[1-9][0-9]*)\Z")
_TIMESTAMP = re.compile(
    r"([0-9]{1,2}):([0-9]{2}) (am|pm) on ([0-9]{1,2}) ([A-Za-z]+), ([0-9]{4})\Z",
    re.IGNORECASE,
)
_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"invalid_{field}_object")
    return value


def _array(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"invalid_{field}_array")
    return value


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"invalid_{field}_string")
    return value


def parse_session_timestamp(value: str) -> datetime:
    """Parse ``h:mm am/pm on d Month, YYYY`` as aware UTC without locale state."""
    if not isinstance(value, str) or (match := _TIMESTAMP.fullmatch(value)) is None:
        raise ValueError("invalid_session_timestamp_format")
    hour, minute, meridiem, day, month, year = match.groups()
    month_number = _MONTHS.get(month.casefold())
    if month_number is None or not 1 <= int(hour) <= 12:
        raise ValueError("invalid_session_timestamp_value")
    hour_number = int(hour) % 12 + (12 if meridiem.casefold() == "pm" else 0)
    try:
        return datetime(int(year), month_number, int(day), hour_number, int(minute), tzinfo=UTC)
    except ValueError as error:
        raise ValueError("invalid_session_timestamp_value") from error


def _source_sessions(conversation: Mapping[str, Any]) -> list[tuple[str, datetime, list[Any]]]:
    """Require an unambiguous date/list pair for each numbered source session."""
    session_numbers: set[int] = set()
    date_numbers: set[int] = set()
    for key in conversation:
        if match := _SESSION.fullmatch(key):
            session_numbers.add(int(match.group(1)))
        elif match := _SESSION_DATE.fullmatch(key):
            date_numbers.add(int(match.group(1)))
        elif key.startswith("session_"):
            raise ValueError("invalid_session_key")
    if not session_numbers or not session_numbers.issubset(date_numbers):
        raise ValueError("session_dates_must_match_session_lists")
    result = []
    for number in sorted(session_numbers):
        key = f"session_{number}"
        source_date = _text(conversation[f"{key}_date_time"], "session_timestamp")
        result.append((key, parse_session_timestamp(source_date), _array(conversation[key], "session_turns")))
    return result


def _gold_answer(question: Mapping[str, Any], category: int) -> str:
    """The category-5 decoy is not a gold fact; its endpoint is abstention."""
    if category == 5 and "answer" not in question:
        return "no information available"
    value = question.get("answer")
    if isinstance(value, str):
        return value
    if type(value) is int or (type(value) is float and isfinite(value)):
        return str(value)
    if isinstance(value, Decimal) and value.is_finite():
        # ijson preserves decimal lexemes; the official json.load scorer uses
        # float text (2.50 -> "2.5"), which matters for exact token scoring.
        numeric = float(value)
        if isfinite(numeric):
            return str(numeric)
    raise ValueError("invalid_gold_answer")


def _gold_evidence(
    question: Mapping[str, Any], dialogue_ids: Mapping[str, str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve compound IDs; preserve malformed/dangling gold without inventing turns."""
    values = _array(question.get("evidence", []), "evidence")
    result = []
    unresolved: list[str] = []
    for value in values:
        source_id = _text(value, "evidence_id").strip()
        if source_id.startswith("(") and source_id.endswith(")"):
            source_id = source_id[1:-1].strip()
        units = re.findall(r"D[1-9][0-9]*:(?:0|[1-9][0-9]*)", source_id)
        remainder = re.sub(r"D[1-9][0-9]*:(?:0|[1-9][0-9]*)", "", source_id)
        if not units or remainder.strip(" ;,()\t\r\n"):
            if source_id not in unresolved:
                unresolved.append(source_id)
            continue
        for unit in units:
            if unit not in dialogue_ids:
                if unit not in unresolved:
                    unresolved.append(unit)
                continue
            mapped = dialogue_ids[unit]
            if mapped not in result:
                result.append(mapped)
    return tuple(result), tuple(unresolved)


def prepare_conversation(row: Mapping[str, Any]) -> PreparedCase:
    """Normalize one original conversation without mutating it or exposing gold.

    All source sessions are replayed before the annotated questions, as required
    for the conversation-level descriptive benchmark. Questions share the final
    recorded reference clock, including R3 microsecond adjustments. No source
    question timestamp is invented for annotations that do not contain one.
    """
    row = _mapping(row, "conversation_record")
    sample_id = _text(row.get("sample_id"), "sample_id")
    conversation = _mapping(row.get("conversation"), "conversation")
    speaker_a = _text(conversation.get("speaker_a"), "speaker_a")
    speaker_b = _text(conversation.get("speaker_b"), "speaker_b")
    if speaker_a == speaker_b:
        raise ValueError("distinct_human_speakers_required")
    speakers = {speaker_a, speaker_b}
    sessions = _source_sessions(conversation)
    questions = _array(row.get("qa"), "qa")
    if not questions:
        raise ValueError("nonempty_qa_required")

    cluster_id = f"LoCoMo:{sample_id}"
    user_id = f"{cluster_id}:conversation"
    replay_sessions = []
    turns_by_id: dict[str, TurnRecord] = {}
    dialogue_ids: dict[str, str] = {}
    source_speakers = []
    for key, timestamp, turns in sessions:
        session_id = f"{cluster_id}:{key}"
        replay_turns = []
        source_session_number = int(key.removeprefix("session_"))
        for value in turns:
            turn = _mapping(value, "turn")
            speaker = _text(turn.get("speaker"), "speaker")
            if speaker not in speakers:
                raise ValueError("unknown_human_speaker")
            source_id = _text(turn.get("dia_id"), "dialogue_id")
            match = _DIALOGUE.fullmatch(source_id)
            if match is None or int(match.group(1)) != source_session_number:
                raise ValueError("invalid_dialogue_session_identity")
            if source_id in dialogue_ids:
                raise ValueError("duplicate_dialogue_id")
            turn_id = f"{cluster_id}:{source_id}"
            dialogue_ids[source_id] = turn_id
            turns_by_id[turn_id] = TurnRecord(
                user_id=user_id,
                session_id=session_id,
                turn_id=turn_id,
                role="user",
                content=_text(turn.get("text"), "turn_text", allow_empty=True),
                occurred_at=timestamp,
                source_speaker=speaker,
            )
            replay_turns.append(ReplayTurn(turn_id=turn_id, source_timestamp=timestamp))
            source_speakers.append((turn_id, speaker))
        replay_sessions.append(ReplaySession(session_id=session_id, timestamp=timestamp, turns=tuple(replay_turns)))

    scheduled = schedule_replay(replay_sessions)
    reference = reference_time(max(timestamp for _, timestamp, _ in sessions), scheduled)
    events: list[IngestEvent | QuestionEvent] = [
        IngestEvent(clock=turn.recorded_clock, turn=turns_by_id[turn.turn.turn_id]) for turn in scheduled
    ]
    gold = []
    for index, value in enumerate(questions):
        question = _mapping(value, "question")
        category = question.get("category")
        if type(category) is not int or category not in {1, 2, 3, 4, 5}:
            raise ValueError("invalid_numeric_category")
        question_id = f"{cluster_id}:q{index:04d}"
        question_record = QuestionRecord(
            question_id=question_id,
            user_id=user_id,
            question=_text(question.get("question"), "question_text"),
            stratum=f"category_{category}",
        )
        events.append(QuestionEvent(clock=reference, question=question_record))
        resolved, unresolved = _gold_evidence(question, dialogue_ids)
        gold.append(
            GoldQuestion(
                question_id=question_id,
                cluster_id=cluster_id,
                user_id=user_id,
                answer=_gold_answer(question, category),
                category=category,
                gold_turn_ids=resolved,
                unresolved_evidence_ids=unresolved,
                abstention=category == 5,
            )
        )
    audit = ReplayAudit(
        original_session_ids=tuple(session.session_id for session in replay_sessions),
        replay_session_ids=tuple(
            session.session_id for session in sorted(replay_sessions, key=lambda session: session.timestamp)
        ),
        clock_adjustments=tuple(
            ClockAdjustment(
                turn_id=turn.turn.turn_id, source_time=turn.turn.source_timestamp, recorded_time=turn.recorded_clock
            )
            for turn in scheduled
            if turn.recorded_clock != turn.turn.source_timestamp
        ),
        source_speakers=tuple(source_speakers),
        unpaired_session_dates=tuple(
            key
            for key in conversation
            if _SESSION_DATE.fullmatch(key) and key.removesuffix("_date_time") not in conversation
        ),
    )
    return PreparedCase(
        case=EvaluationCase(cluster_id=cluster_id, user_ids=(user_id,), events=tuple(events)),
        gold=tuple(gold),
        audit=audit,
    )


class LoCoMoAdapter(BenchmarkSource):
    """Guarded, checksum-verified source facade; construction never reads data."""

    def __init__(self, raw_path: Path, *, manifest_path: Path = MANIFEST_PATH) -> None:
        super().__init__(
            raw_path=raw_path,
            manifest_path=manifest_path,
            filename=FILENAME,
            suite_id="LoCoMo",
            normalizer=prepare_conversation,
            record_id=lambda row: _text(row.get("sample_id"), "sample_id"),
        )
