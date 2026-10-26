"""One answer format and deterministic context rendering for every baseline."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from evaluation.baselines.contracts import BaselineSettings, Message, Tokenizer
from evaluation.harness.models import QuestionRecord, RetrievedItem, TurnRecord


def answer_messages(
    settings: BaselineSettings, question: QuestionRecord, rendered_context: str, reference_clock: datetime
) -> tuple[Message, ...]:
    """Expose replay time without treating target validity time as recorded time."""
    temporal = f"Reference time (UTC): {_utc_iso(reference_clock)}\nQuery mode: {question.mode}"
    if question.as_of_time is not None:
        temporal += f"\nTarget time (UTC): {_utc_iso(question.as_of_time)}"
    return (
        Message(role="system", content=settings.answer_prompt),
        Message(role="user", content=f"{temporal}\n\nContext:\n{rendered_context}\n\nQuestion:\n{question.question}"),
    )


def _utc_iso(timestamp: datetime) -> str:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("aware_answer_timestamp_required")
    return timestamp.astimezone(UTC).isoformat()


def format_turn(turn: TurnRecord, *, content_override: str | None = None, chunk_id: str | None = None) -> str:
    """Keep role, source speaker and source time distinct from verbatim turn text."""
    metadata = {
        "occurred_at": turn.occurred_at.isoformat(),
        "role": turn.role,
        "session_id": turn.session_id,
        "source_speaker": turn.source_speaker,
        "turn_id": turn.turn_id,
    }
    if chunk_id is not None:
        metadata["chunk_id"] = chunk_id
    return (
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        + (turn.content if content_override is None else content_override)
    )


def join_items(items: Sequence[RetrievedItem]) -> str:
    return "\n\n".join(item.content for item in items)


def turn_item(turn: TurnRecord) -> RetrievedItem:
    return RetrievedItem(reference_id=turn.turn_id, content=format_turn(turn), evidence_turn_ids=(turn.turn_id,))


def history_items(
    turns: Sequence[TurnRecord],
    question: QuestionRecord,
    settings: BaselineSettings,
    tokenizer: Tokenizer,
    reference_clock: datetime,
) -> tuple[tuple[RetrievedItem, ...], int]:
    """Drop oldest whole turns until the complete answer request fits its window."""
    if (
        tokenizer.count_messages(answer_messages(settings, question, "", reference_clock))
        + settings.request_overhead_tokens
        + settings.max_output_tokens
    ) > (settings.context_window_tokens):
        raise ValueError("question_exceeds_answer_context_window")
    items = tuple(turn_item(turn) for turn in turns)
    original_tokens = len(tokenizer.encode(join_items(items)))
    while items and (
        tokenizer.count_messages(answer_messages(settings, question, join_items(items), reference_clock))
        + settings.request_overhead_tokens
        + settings.max_output_tokens
        > settings.context_window_tokens
    ):
        items = items[1:]
    return items, max(0, original_tokens - len(tokenizer.encode(join_items(items))))


def fit_ranked_items(
    items: Sequence[RetrievedItem], tokenizer: Tokenizer, token_budget: int
) -> tuple[tuple[RetrievedItem, ...], int]:
    """Use native rank order; only a final visible item may be token-truncated."""
    selected: list[RetrievedItem] = []
    original_tokens = len(tokenizer.encode(join_items(items)))
    for item in items:
        if len(tokenizer.encode(join_items((*selected, item)))) <= token_budget:
            selected.append(item)
            continue
        # Encode the assembled candidate, so separator/Unicode token interactions
        # count against the same budget as the actual rendered string.
        tokens = tokenizer.encode(item.content)
        low, high = 0, len(tokens)
        while low < high:
            middle = (low + high + 1) // 2
            partial = item.model_copy(update={"content": tokenizer.decode(tokens[:middle])})
            if len(tokenizer.encode(join_items((*selected, partial)))) <= token_budget:
                low = middle
            else:
                high = middle - 1
        if low:
            partial = item.model_copy(update={"content": tokenizer.decode(tokens[:low])})
            # A byte-BPE prefix may end in a partial Unicode code point. Keep an
            # exact source-text prefix rather than inserting replacement text.
            while low and (
                not item.content.startswith(partial.content)
                or len(tokenizer.encode(join_items((*selected, partial)))) > token_budget
            ):
                low -= 1
                partial = item.model_copy(update={"content": tokenizer.decode(tokens[:low])})
            if partial.content:
                selected.append(partial)
        break
    result = tuple(selected)
    return result, max(0, original_tokens - len(tokenizer.encode(join_items(result))))


pack_ranked_items = fit_ranked_items
