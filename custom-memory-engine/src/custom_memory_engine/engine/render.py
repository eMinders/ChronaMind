"""Token-budgeted context rendering (RL-051, Proposal §7.3).

Rendering rules, applied mechanically so a caller cannot bypass them:

1. Only ``status = 'active'`` versions are renderable — superseded and archived
   rows are dropped even if a caller passes them in.
2. In ``current`` mode, versions whose ``valid_range`` has already ended are not
   rendered; they belong to ``historical_as_of``.
3. Items are added in rank order until the token budget is reached; the first
   item that does not fit stops selection, so output order equals rank order.
4. Every line carries its validity interval and role label.

Token counting is injected: the engine never imports a tokenizer so it stays
usable with the evaluation harness's frozen tokenizer.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from custom_memory_engine.engine.predicates import TemporalQueryError, TemporalVersion

type TokenCounter = Callable[[str], int]
type RenderMode = str


class RenderError(TemporalQueryError):
    """Static render contract error, reusing the temporal SQLSTATE 22023 channel."""


@dataclass(frozen=True)
class RenderedItem:
    version: TemporalVersion
    label: str
    text: str
    tokens: int


@dataclass(frozen=True)
class RenderedContext:
    mode: str
    items: tuple[RenderedItem, ...]
    text: str
    tokens: int
    truncated_items: int
    truncated_tokens: int


def default_token_counter(text: str) -> int:
    """Whitespace proxy used only when no frozen tokenizer is supplied.

    Reports its own identity through the caller's manifest; never presented as a
    model-accurate count.
    """
    return len(text.split())


def _instant(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def role_for(version: TemporalVersion, mode: str, reference_time: datetime | None) -> str:
    """Label a version by its temporal relationship to the query."""
    if mode == "current" or reference_time is None:
        return "current"
    upper = _instant(version.valid_range.upper)
    lower = _instant(version.valid_range.lower)
    if upper is not None and upper <= reference_time:
        return "previous"
    if lower is not None and lower > reference_time:
        return "future"
    return "current"


def _line(version: TemporalVersion, role: str) -> str:
    lower = version.valid_range.lower
    upper = version.valid_range.upper
    start = lower.isoformat() if isinstance(lower, datetime) else "-infinity"
    end = upper.isoformat() if isinstance(upper, datetime) else "open"
    return f"[{role} | valid {start}..{end}] {version.content}"


def _is_historical(version: TemporalVersion, reference_time: datetime | None) -> bool:
    if reference_time is None:
        return False
    upper = _instant(version.valid_range.upper)
    return upper is not None and upper <= reference_time


def is_renderable(version: TemporalVersion, *, mode: str, reference_time: datetime | None) -> bool:
    """Apply rules 1 and 2 without consulting ranking."""
    if version.status != "active":
        return False
    return not (mode == "current" and _is_historical(version, reference_time))


def render_context(
    versions: Sequence[TemporalVersion],
    *,
    mode: str,
    token_budget: int,
    reference_time: datetime | None = None,
    count_tokens: TokenCounter | None = None,
) -> RenderedContext:
    """Select and format versions within ``token_budget``.

    ``versions`` must already be in rank order; this function never re-sorts, so
    the caller's ranking decision is preserved exactly.
    """
    if not isinstance(token_budget, int) or isinstance(token_budget, bool) or token_budget < 0:
        raise RenderError("nonnegative_token_budget_required")
    counter = count_tokens or default_token_counter

    items: list[RenderedItem] = []
    used = 0
    truncated_items = 0
    truncated_tokens = 0
    stopped = False
    for version in versions:
        if not is_renderable(version, mode=mode, reference_time=reference_time):
            truncated_items += 1
            continue
        if stopped:
            truncated_items += 1
            truncated_tokens += counter(version.content)
            continue
        role = role_for(version, mode, reference_time)
        text = _line(version, role)
        cost = counter(text)
        if items and used + cost > token_budget:
            truncated_items += 1
            truncated_tokens += cost
            stopped = True
            continue
        if not items and cost > token_budget and token_budget > 0:
            # Never exceed the budget, even for a single oversized item.
            truncated_items += 1
            truncated_tokens += cost
            stopped = True
            continue
        items.append(RenderedItem(version=version, label=role, text=text, tokens=cost))
        used += cost

    return RenderedContext(
        mode=mode,
        items=tuple(items),
        text="\n".join(item.text for item in items),
        tokens=used,
        truncated_items=truncated_items,
        truncated_tokens=truncated_tokens,
    )
