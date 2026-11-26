"""Result handles and the rule-based drill-down of ``refine`` (E-034; ``API_V2.md`` §4.2, ``V2-D83``).

``refine(ctx, result_id, instruction) → QueryResult`` drills into a prior ``query`` result **within the same read
set** and cannot widen it. That one line hides three decisions, and this module makes each of them executable:

**The handle.** Nothing stored a result before this task: ``query`` returned a :class:`QueryResult` and the value was
gone. A ``result_id`` is therefore invented here as an opaque, random 128-bit handle (``res_`` plus 32 hex digits)
held in a short-lived, **process-local** map. It is not a digest of the result and not derivable from anything the
caller knows, so it cannot be forged: a lookup is an exact match on a value only the engine has seen. The map is
bounded (:data:`MAX_RESULTS` entries) and time-limited (:data:`RESULT_TTL`), with an injected clock so expiry is
testable.

A handle that is unknown, **foreign** (another principal's) or **expired** is *one failed lookup* and therefore one
answer, ``forbidden``, indistinguishably (``THREAT_MODEL.md`` T-11): the caller learns nothing about whether a result
exists for somebody else. A process restart drops every handle. That is acceptable for a drill-down — the caller
asks again — and it is the reason a result is **not** persisted: a result is not a memory, so it has no row, and
there is nothing to purge. A deployment with several workers must route a ``refine`` to the process that answered the
``query``, or the handle is unknown there; that is the same fail-closed answer, never a widening.

**Read-set containment.** ``CallerContext.read_set`` is resolved per call from the live credential
(``api.entry.CallerContext.from_credential``), so it can change between the two calls. The handle therefore records
the read set of the **original** call, plus the intent that produced it, and a refine re-plans from exactly that. A
credential that has **narrowed** since is refused (``forbidden``, before any statement); one that has **widened**
still reads only the recorded scopes, so a drill-down can never see more than the result it drills into. That is the
security property that makes this more than "query again with a filter".

**The instruction.** Interpretation is rule-based, replayable and total (``E-031``'s stance, no model): one operator
per instruction — ``artifact``, ``channel``, ``mention``, ``scope`` — matched exhaustively over a
:type:`NarrowingOperator`. Anything else is a documented error, never a silent no-op that returns the same result.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_hex
from typing import Literal

from custom_memory_engine.engine.channels import ChannelBudgets
from custom_memory_engine.engine.planning import Intent
from custom_memory_engine.scope.access import ReadSet
from custom_memory_engine.scope.paths import ScopeError, parse_scope

RESULT_PREFIX = "res_"
RESULT_HEX_LENGTH = 32  # 128 bits
#: How long a drill-down handle stays live. Short on purpose: a result is answer-context material, not a record.
RESULT_TTL = timedelta(minutes=15)
#: At most this many live handles per engine; the oldest issued is evicted first once the map is full.
MAX_RESULTS = 1024
#: How many refines a chain may chain: the handle records its depth and the next one is refused.
MAX_REFINE_DEPTH = 4
_RESULT = re.compile(rf"{RESULT_PREFIX}[0-9a-f]{{{RESULT_HEX_LENGTH}}}\Z")

#: Every operator the rule-based interpreter knows, and nothing else (``API_V2.md`` §4.2).
type NarrowingOperator = Literal["artifact", "channel", "mention", "scope"]
NARROWING_OPERATORS: tuple[NarrowingOperator, ...] = ("artifact", "channel", "mention", "scope")
#: The four channel names a ``channel`` instruction may name (``CHANNELS.md``, frozen by ``V2-D48``).
NARROWING_CHANNELS: frozenset[str] = frozenset({"facts", "hypotheses", "procedures", "conflicts"})

INSTRUCTION_REQUIRED = "instruction_required"
INSTRUCTION_UNRECOGNISED = "instruction_unrecognised"


class InstructionError(ValueError):
    """Static code; never echoes the instruction text, an id or a scope."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Instruction:
    """One narrowing the caller asked for: an operator and its argument.

    ``keeps`` is the whole of the interpretation. ``channel`` is passed in by the caller because membership is
    :func:`custom_memory_engine.engine.channels.channel_of`'s decision (a pure function of the artifact's own tuple),
    never this module's.
    """

    operator: NarrowingOperator
    argument: str

    def __post_init__(self) -> None:
        if self.operator not in NARROWING_OPERATORS or not isinstance(self.argument, str) or not self.argument.strip():
            raise InstructionError(INSTRUCTION_UNRECOGNISED)
        if self.operator == "channel" and self.argument not in NARROWING_CHANNELS:
            raise InstructionError(INSTRUCTION_UNRECOGNISED)

    def keeps(self, *, artifact_id: str, scope_path: str, content: str, channel: str | None) -> bool:
        """Whether one retrieved row survives this instruction. Exhaustive over the operator vocabulary."""
        match self.operator:
            case "artifact":
                return artifact_id == self.argument
            case "channel":
                return channel == self.argument
            case "mention":
                return self.argument.casefold() in content.casefold()
            case "scope":
                # both sides are canonical (``scope.paths``): a trailing separator makes the prefix a segment prefix
                return scope_path == self.argument or scope_path.startswith(self.argument)


def parse_instruction(value: object) -> Instruction:
    """Parse one instruction, or raise a static :class:`InstructionError`.

    An absent or blank instruction is ``instruction_required``; an operator no rule understands, a missing argument,
    a multi-word argument to an operator other than ``mention``, an unknown channel or a malformed scope is
    ``instruction_unrecognised``. There is no default operator and no
    "return the result unchanged" path: an unrecognised drill-down is an error, not a no-op (requirement 3).
    """
    if not isinstance(value, str) or not value.strip():
        raise InstructionError(INSTRUCTION_REQUIRED)
    operator, _, argument = value.strip().partition(" ")
    argument = argument.strip()
    if not argument:
        raise InstructionError(INSTRUCTION_UNRECOGNISED)
    # An id, a channel name and a canonical path are single tokens: whitespace inside one means a second operator or a
    # stray word, and silently matching nothing would be indistinguishable from a real empty drill-down. Only
    # ``mention`` takes free text.
    if operator.casefold() != "mention" and len(argument.split()) != 1:
        raise InstructionError(INSTRUCTION_UNRECOGNISED)
    match operator.casefold():
        case "artifact":
            return Instruction("artifact", argument)
        case "channel":
            return Instruction("channel", argument.casefold())
        case "mention":
            return Instruction("mention", argument)
        case "scope":
            try:
                return Instruction("scope", parse_scope(argument).text)
            except ScopeError:
                raise InstructionError(INSTRUCTION_UNRECOGNISED) from None
        case _:
            raise InstructionError(INSTRUCTION_UNRECOGNISED)


@dataclass(frozen=True, slots=True)
class ResultSnapshot:
    """What a handle remembers about the ``query`` it names — and everything a refine is allowed to know.

    ``read_set`` is the read set of the **original** call, not the refining one (containment). ``intent`` is what the
    planner was given, so a refine re-enters the planner instead of reaching into the assembler. ``budgets`` are the
    token ceilings that answer used, because a refine has no ``request`` to read them from and defaulting would
    re-assemble to a different answer than the one being drilled into. ``candidates`` are the ids the original
    result named: a refine may return a subset of them and nothing else.
    """

    principal: str
    read_set: ReadSet
    intent: Intent
    budgets: ChannelBudgets
    candidates: tuple[str, ...]
    expires_at: datetime
    depth: int = 0

    def expired(self, now: datetime) -> bool:
        return self.expires_at <= now


@dataclass(frozen=True, slots=True)
class Narrowing:
    """The two restrictions a refine's execution carries: the prior candidate ids and the instruction's filter."""

    candidates: tuple[str, ...]
    instruction: Instruction


def utc_now() -> datetime:
    """The default clock for handle expiry. Injected, so a test decides when a handle expires."""
    return datetime.now(UTC)


class ResultStore:
    """The short-lived, process-local home of result handles (``V2-D83``).

    Not a table on purpose: a result is answer-context material, not a memory, so it must not be persisted, searched
    or purged. The store is bounded and self-cleaning, so a caller who never refines costs one dict entry for
    :data:`RESULT_TTL`.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = RESULT_TTL,
        capacity: int = MAX_RESULTS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise ValueError("result_ttl_must_be_positive")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("result_capacity_must_be_a_positive_integer")
        self._ttl = ttl
        self._capacity = capacity
        self._clock = clock
        self._entries: dict[str, ResultSnapshot] = {}
        self._principals: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def issue(
        self,
        *,
        principal: str,
        read_set: ReadSet,
        intent: Intent,
        budgets: ChannelBudgets,
        candidates: Sequence[str],
        depth: int = 0,
    ) -> str:
        """Mint a handle for one result and record what a drill-down needs. Returns the opaque id."""
        now = self._now()
        self._drop_expired(now)
        bucket = self._principals.setdefault(principal, OrderedDict())
        self._principals.move_to_end(principal)
        while len(self._principals) > self._capacity:
            _, handles = self._principals.popitem(last=False)
            for old_handle in handles:
                del self._entries[old_handle]
        handle = RESULT_PREFIX + token_hex(RESULT_HEX_LENGTH // 2)
        self._entries[handle] = ResultSnapshot(
            principal=principal,
            read_set=read_set,
            intent=intent,
            budgets=budgets,
            candidates=tuple(candidates),
            depth=depth,
            expires_at=now + self._ttl,
        )
        bucket[handle] = None
        self._make_room(principal)
        return handle

    def resolve(self, principal: str, result_id: object) -> ResultSnapshot | None:
        """The snapshot behind ``result_id`` for ``principal``, or ``None`` when unknown, foreign or expired.

        The three are one answer on purpose (``T-11``): a caller must not be able to tell a handle that exists for
        somebody else from one that never existed.
        """
        if not isinstance(result_id, str) or _RESULT.fullmatch(result_id) is None:
            return None
        snapshot = self._entries.get(result_id)
        if snapshot is None or snapshot.principal != principal:
            return None
        if snapshot.expired(self._now()):
            self._remove(result_id, principal)  # an expired handle is gone, not merely unusable
            return None
        self._principals.move_to_end(principal)
        return snapshot

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:  # a naive instant cannot order expiry
            raise ValueError("result_clock_must_be_timezone_aware")
        return now

    def _drop_expired(self, now: datetime) -> None:
        for handle, snapshot in list(self._entries.items()):
            if snapshot.expired(now):
                self._remove(handle, snapshot.principal)

    def _remove(self, handle: str, principal: str) -> None:
        del self._entries[handle]
        handles = self._principals[principal]
        del handles[handle]
        if not handles:
            del self._principals[principal]

    def _make_room(self, active_principal: str) -> None:
        while len(self._entries) > self._capacity:
            active_handles = self._principals[active_principal]
            if len(active_handles) > 1:
                old_handle, _ = active_handles.popitem(last=False)
                del self._entries[old_handle]
                continue
            surplus = next(
                ((principal, handles) for principal, handles in self._principals.items() if len(handles) > 1),
                None,
            )
            if surplus is not None:
                _, handles = surplus
                old_handle, _ = handles.popitem(last=False)
                del self._entries[old_handle]
                continue
            _, handles = self._principals.popitem(last=False)
            for old_handle in handles:
                del self._entries[old_handle]


__all__ = [
    "INSTRUCTION_REQUIRED",
    "INSTRUCTION_UNRECOGNISED",
    "MAX_REFINE_DEPTH",
    "MAX_RESULTS",
    "NARROWING_CHANNELS",
    "NARROWING_OPERATORS",
    "RESULT_PREFIX",
    "RESULT_TTL",
    "Instruction",
    "InstructionError",
    "Narrowing",
    "NarrowingOperator",
    "ResultSnapshot",
    "ResultStore",
    "parse_instruction",
    "utc_now",
]
