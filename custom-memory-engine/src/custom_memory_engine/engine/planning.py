"""The retrieval planner: intent in, an explicit :class:`Plan` out (E-031, ``ARCHITECTURE_V2.md`` §9).

Retrieval is **staged** (``V2-D11``). Before this module, ``query`` decided everything inline: the request arrived and
the engine chose the predicate, the filters and the limit on the way to a statement. That made retrieval
unexplainable, unrecordable and untunable, and left ``E-036`` with nothing to attribute cost to.

The shape this module fixes is the one §9 draws, with a deliberate narrowing: **planning is a pure function and
execution is a separate function.** ``plan(intent, …) -> Plan`` performs no I/O, reads no clock and touches no
connection, so the same intent yields a byte-identical plan on every call (requirement 1) and a recorded plan can be
replayed (§3.5). :meth:`custom_memory_engine.api.read_handlers.ReadHandlers._execute_plan` runs it.

**What the planner decides, and what it deliberately does not.** The plan names the index the statement's predicate
shape matches, the ordered filter list, the expansion intent, whether the result is routed to answer channels or to
``audit``, and the stage list. It does **not** decide channel membership (:func:`~.channels.channel_of` is a pure
function of the artifact's own tuple, ``CHANNELS.md``), nor the ranking (``V2-D75`` puts procedure ordering after
routing), nor the row limit (the request's own). A planner that could influence any of those three would be a way to
turn a filter into a score, which ``ARCHITECTURE_V2.md`` §9.3 forbids:

    Non-negotiable: temporal predicate is a filter, not a score term.

**Determinism.** Every collection in a :class:`Plan` has a total order and :meth:`Plan.to_dict` emits it as declared,
so two identical intents serialise to identical bytes. Nothing here iterates a set into the plan: the visibility
frozensets of a read set are sorted before they enter, and the filter and stage lists are fixed tuples, not derived
from dict or set iteration.

**Profile-independence.** Nothing in this module branches on a profile name (requirement 8).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from custom_memory_engine.scope.access import ReadSet

if TYPE_CHECKING:
    # Type only: importing ``engine.predicates`` at run time would pull the connection layer (psycopg) into a module
    # whose contract is that it does no I/O (criterion 3, ``test_the_planner_does_no_io``).
    from custom_memory_engine.engine.predicates import QueryMode

# The five stages of ``ARCHITECTURE_V2.md`` §9, named with the words ``E-036`` (#206) measures, so the planned stages
# and the measured stages are one list and cannot drift (requirement 5 of E-036, criterion 10 here).
STAGES: tuple[str, ...] = ("plan", "candidate_generation", "expansion", "filter_rerank", "assemble")
ASSOCIATION_EXPANSION = "association"

#: The hop budget a plan records when it expands, and the one this engine executes (``E-032-F3``). Restated rather than
#: imported: ``engine.expansion`` pulls in the connection layer, and this module's contract is that it does no I/O
#: (criterion 3). ``tests/test_planning.py`` pins the two to each other so they cannot drift silently.
DEFAULT_HOP_BUDGET = 1

#: Indexes the planner may select, each of which exists in the schema (see this issue's §4.4). A planner that named
#: an index the database does not have would be a plan for a database that does not exist.
#
#: ``memory_version_temporal_idx`` is gist ``(user_id, event_range, recorded_range)`` under
#: ``status IN ('active','archived')`` -- exactly the columns :func:`~.predicates.compile_predicate` filters on.
TEMPORAL_INDEX = "memory_version_temporal_idx"

#: The route a plan takes. ``channels`` yields the four answer channels; ``audit`` yields ``audit[]`` and must never
#: reach one (``TECHNICAL_SPECIFICATION.md`` §3.5, ``V2-D73``).
type Route = Literal["channels", "audit"]

#: ``API_V2.md`` §4.2's modes minus ``change`` (``E-033``). Stated here rather than imported, so this module stays
#: free of the connection layer; ``test_the_planner_modes_are_the_predicate_modes`` pins it to
#: ``predicates.QueryMode`` so the two cannot drift.
MODES: frozenset[str] = frozenset({"current_state", "historical_as_of", "historical_window", "audit_as_of"})

#: An intent the planner does not recognise is refused, never defaulted (recommended 3 on the issue): a silent
#: default is indistinguishable from a bug in a replay.
PLAN_INTENT_REFUSED = "intent_not_planned"


class PlanningError(ValueError):
    """A plan could not be produced from this intent. The ``code`` is a static literal for the entry point's error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Intent:
    """What the caller asked for, **derived from the request's own fields** (no parallel vocabulary, §5.1).

    ``mode``, ``limit`` and the instants the mode needs come from ``QueryRequest``. ``scope`` is the **canonical**
    path the request named, or ``None``: it is not an authorization input (``query`` has already re-anchored it inside
    the caller's read set, and the planner receives that read set), it is what procedure applicability is matched
    against (``E-030``'s ``scope_within``). No ``reference_time``: ρ is the engine clock and stays refused
    (``V2-D73``), so it is never part of an intent and the planner never sees one.
    """

    mode: QueryMode
    limit: int
    scope: str | None = None
    target_time: datetime | None = None
    belief_time: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise PlanningError(PLAN_INTENT_REFUSED)
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
            raise PlanningError(PLAN_INTENT_REFUSED)


@dataclass(frozen=True, slots=True)
class ReadScope:
    """One authorized read selector, frozen so a plan can carry (and replay) it.

    ``visibilities`` is a **sorted tuple**, not a set: a set would serialise in iteration order and break the byte
    equality criterion 2 asserts.
    """

    path: str
    mode: str
    visibilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.mode not in ("subtree", "exact"):
            raise PlanningError(PLAN_INTENT_REFUSED)
        if not self.path:
            raise PlanningError(PLAN_INTENT_REFUSED)


def read_scopes(read_set: ReadSet) -> tuple[ReadScope, ...]:
    """The read set as plan-carriable scopes, in a **total order** (by path, then mode).

    The order is the plan's, not the read set's, so two structurally equal read sets built in different insertion
    orders produce the same bytes. Visibilities are sorted for the same reason.
    """
    return tuple(
        sorted(
            (ReadScope(entry.path, entry.mode, tuple(sorted(entry.visibilities))) for entry in read_set.entries),
            key=lambda scope: (scope.path, scope.mode),
        )
    )


@dataclass(frozen=True, slots=True)
class Plan:
    """An inspectable, serialisable description of the work ``query`` will do.

    Frozen with immutable fields, so "is this plan equal to that one" is a real question with a real answer
    (recommended 1). ``filters`` and ``stages`` are tuples in their execution order; every other collection that enters
    is sorted by :func:`read_scopes`.
    """

    mode: QueryMode
    route: Route
    index: str
    filters: tuple[str, ...]
    expansion: str
    # The traversal depth expansion runs at (``E-032-F3``). A budget is a performance parameter, which is exactly why
    # a plan records it: replaying under a different default must not silently traverse a different graph.
    hop_budget: int
    rows: int
    scopes: tuple[ReadScope, ...]
    # The caller's instants, as canonical UTC ISO-8601 text, and only those the mode uses. They are part of the plan so
    # a recorded plan replays the *same* question; ρ is not here, because it is the engine clock read at execution.
    target_time: str | None = None
    belief_time: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    # The request's canonical scope, which procedure applicability is matched against (E-030). Not an authorization
    # input: what may be read is `scopes`, and `execute_plan` refuses any scope the caller does not hold.
    scope: str | None = None
    stages: tuple[str, ...] = STAGES

    def __post_init__(self) -> None:
        if self.route not in ("channels", "audit"):
            raise PlanningError(PLAN_INTENT_REFUSED)
        if (self.route == "audit") != (self.mode == "audit_as_of"):
            # V2-D73: an audit row is a past belief and never answer context, and nothing else is audit. A plan that
            # routed audit rows into channels (or answer rows into audit) is unrepresentable, not merely untested.
            raise PlanningError(PLAN_INTENT_REFUSED)
        if isinstance(self.rows, bool) or not isinstance(self.rows, int) or self.rows < 1:
            raise PlanningError(PLAN_INTENT_REFUSED)
        if (
            self.filters != _filters_for(self.mode)
            or self.index != TEMPORAL_INDEX
            or self.expansion not in ("none", ASSOCIATION_EXPANSION)
        ):
            # a recorded plan replays only what this engine can execute; anything else is refused, not reinterpreted
            raise PlanningError(PLAN_INTENT_REFUSED)
        if isinstance(self.hop_budget, bool) or not isinstance(self.hop_budget, int):
            raise PlanningError(PLAN_INTENT_REFUSED)
        if self.expansion == "none" and self.hop_budget != 0:
            # following no association for a nonzero depth is a contradiction, not a preference
            raise PlanningError(PLAN_INTENT_REFUSED)
        if self.expansion == ASSOCIATION_EXPANSION and self.hop_budget < 1:
            # expansion at a zero budget reaches nothing; a plan that records expansion must traverse at least one hop
            raise PlanningError(PLAN_INTENT_REFUSED)
        present = {
            name
            for name in ("target_time", "belief_time", "window_start", "window_end")
            if getattr(self, name) is not None
        }
        if present != _INSTANTS[self.mode]:
            raise PlanningError(PLAN_INTENT_REFUSED)
        if self.stages != STAGES:
            # the stage list is E-036's measurement contract; a plan that invents stages would drift from it
            raise PlanningError(PLAN_INTENT_REFUSED)

    def to_dict(self) -> dict[str, object]:
        """A JSON-safe rendering, in declared order so equal plans render equal bytes."""
        return {
            "mode": self.mode,
            "route": self.route,
            "index": self.index,
            "filters": list(self.filters),
            "expansion": self.expansion,
            # Only an expanding plan carries a budget: "none" already means it traverses nothing, so emitting a 0 would
            # add a field to every pre-E-032 record for no information. An expanding plan must carry it, which is the
            # whole of E-032-F3 -- a record that expands without saying how far cannot be replayed faithfully.
            **({"hop_budget": self.hop_budget} if self.expansion != "none" else {}),
            "rows": self.rows,
            "scopes": [{"path": s.path, "mode": s.mode, "visibilities": list(s.visibilities)} for s in self.scopes],
            "target_time": self.target_time,
            "belief_time": self.belief_time,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "scope": self.scope,
            "stages": list(self.stages),
        }

    def instant(self, name: str) -> datetime | None:
        """One of the plan's instants as an aware ``datetime`` (the executor's input to the temporal predicate)."""
        raw = getattr(self, name)
        return None if raw is None else datetime.fromisoformat(raw)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Plan:
        """Rebuild a recorded plan, so a replay runs the plan that was recorded rather than a re-plan of it."""
        try:
            mode = payload["mode"]
            route = payload["route"]
            index = payload["index"]
            filters = payload["filters"]
            expansion = payload["expansion"]
            if "hop_budget" in payload:
                hop_budget = payload["hop_budget"]
            elif expansion == "none":
                hop_budget = 0  # a no-expansion record is complete without a budget: it traverses nothing
            else:
                # An expanding record with no budget is exactly the ambiguity E-032-F3 describes: replaying it would
                # silently use whatever the current default is. Refused, not guessed.
                raise PlanningError(PLAN_INTENT_REFUSED)
            rows = payload["rows"]
            scopes = payload["scopes"]
            stages = payload["stages"]
        except KeyError as exc:  # a truncated record must not replay as a different query
            raise PlanningError(PLAN_INTENT_REFUSED) from exc
        if (
            not isinstance(mode, str)
            or mode not in MODES
            or not isinstance(route, str)
            or not isinstance(index, str)
            or not isinstance(expansion, str)
            or not isinstance(hop_budget, int)
            or isinstance(hop_budget, bool)
            or not isinstance(rows, int)
            or isinstance(rows, bool)
            or not isinstance(filters, Sequence)
            or not isinstance(stages, Sequence)
            or not isinstance(scopes, Sequence)
        ):
            raise PlanningError(PLAN_INTENT_REFUSED)
        if not all(isinstance(item, str) for item in filters) or not all(isinstance(item, str) for item in stages):
            raise PlanningError(PLAN_INTENT_REFUSED)
        instants: dict[str, str | None] = {}
        for name in ("target_time", "belief_time", "window_start", "window_end"):
            raw = payload.get(name)
            if raw is not None:
                try:
                    canonical = _canonical_instant(raw) if isinstance(raw, str) else None
                except ValueError as exc:
                    raise PlanningError(PLAN_INTENT_REFUSED) from exc
                if canonical != raw:  # only the planner's own rendering replays; a reinterpretation does not
                    raise PlanningError(PLAN_INTENT_REFUSED)
            instants[name] = raw if isinstance(raw, str) else None
        request_scope = payload.get("scope")
        if request_scope is not None and not isinstance(request_scope, str):
            raise PlanningError(PLAN_INTENT_REFUSED)
        rebuilt: list[ReadScope] = []
        for scope in scopes:
            if not isinstance(scope, Mapping):
                raise PlanningError(PLAN_INTENT_REFUSED)
            path, scope_mode, visibilities = scope.get("path"), scope.get("mode"), scope.get("visibilities")
            if (
                not isinstance(path, str)
                or not isinstance(scope_mode, str)
                or not isinstance(visibilities, Sequence)
                or not all(isinstance(item, str) for item in visibilities)
            ):
                raise PlanningError(PLAN_INTENT_REFUSED)
            rebuilt.append(ReadScope(path, scope_mode, tuple(visibilities)))
        try:
            return cls(
                mode=mode,  # type: ignore[arg-type]
                route=route,  # type: ignore[arg-type]
                index=index,
                filters=tuple(filters),  # type: ignore[arg-type]
                expansion=expansion,
                hop_budget=hop_budget,
                rows=rows,
                scopes=tuple(rebuilt),
                stages=tuple(stages),  # type: ignore[arg-type]
                target_time=instants["target_time"],
                belief_time=instants["belief_time"],
                window_start=instants["window_start"],
                window_end=instants["window_end"],
                scope=request_scope,
            )
        except PlanningError:
            raise
        except (TypeError, ValueError) as exc:
            raise PlanningError(PLAN_INTENT_REFUSED) from exc


#: Which instants each mode carries (``TemporalQuery.mode_and_guards`` minus ρ, which is the engine's, not the plan's).
_INSTANTS: Mapping[str, frozenset[str]] = {
    "current_state": frozenset(),
    "historical_as_of": frozenset({"target_time"}),
    "historical_window": frozenset({"window_start", "window_end"}),
    "audit_as_of": frozenset({"target_time", "belief_time"}),
}

#: The filters every mode applies, in execution order. The first three are one SQL statement, before ``LIMIT``
#: (``fetch_scoped_rows``): filtering after the limit would silently return fewer rows than exist. The fourth is the
#: ``V2-D45`` defence-in-depth re-check in ``filter_rerank``, which drops nothing in a correct build.
#:
#: The temporal predicate is a *filter* and is listed here, never in a score (``ARCHITECTURE_V2.md`` §9.3). Its exact
#: clauses differ per mode (``compile_predicate``), but which clauses is the mode's business, already in the plan.
_FILTERS: tuple[str, ...] = ("temporal_predicate", "typed_artifact", "read_predicate", "row_visible_recheck")

#: ``E-030``: a non-applicable procedure is removed in ``filter_rerank``, **before** routing (``V2-D75`` puts ranking
#: after routing; a filter belongs before it). It only removes, so it cannot move an item between channels.
PROCEDURE_APPLICABILITY = "procedure_applicability"


def _filters_for(mode: str) -> tuple[str, ...]:
    """The ordered filter list for a mode.

    Answer routes add procedure applicability. ``audit_as_of`` does not: an audit shows what the engine *believed*,
    and a procedure's applicability to the present request says nothing about whether it was believed then. Filtering
    it would hide an audit row for a reason that is not about belief.
    """
    return _FILTERS if mode == "audit_as_of" else (*_FILTERS, PROCEDURE_APPLICABILITY)


def _canonical_instant(value: str | datetime) -> str:
    """An aware instant as UTC ISO-8601 text, so equal instants serialise to equal bytes whatever their offset."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise PlanningError(PLAN_INTENT_REFUSED)
    return parsed.astimezone(UTC).isoformat()


@runtime_checkable
class Planner(Protocol):
    """The model-injection seam: one method, so ``W4``/``W5`` can supply a model without touching the callers.

    The rule-based implementation is the only one today and no model call is made (requirement 5, criterion 9). A
    future implementation must still return a :class:`Plan`, which is what keeps the executor and `E-036`'s stages
    independent of *how* a plan was chosen.
    """

    def plan(self, intent: Intent, scopes: Sequence[ReadScope]) -> Plan: ...


@dataclass(frozen=True, slots=True)
class RuleBasedPlanner:
    """The v1 planner: a total function of the intent and the read scopes. Pure, no I/O, no clock, no profile."""

    def plan(self, intent: Intent, scopes: Sequence[ReadScope]) -> Plan:
        """Choose the index, the filters, the expansion intent and the route for ``intent``."""
        route: Route = "audit" if intent.mode == "audit_as_of" else "channels"
        # Only the instants this mode uses enter the plan; an instant the mode ignores is not part of the question.
        wanted = _INSTANTS[intent.mode]

        def kept(name: str, value: datetime | None) -> str | None:
            return _canonical_instant(value) if name in wanted and value is not None else None

        return Plan(
            mode=intent.mode,
            route=route,
            index=TEMPORAL_INDEX,
            filters=_filters_for(intent.mode),
            expansion=ASSOCIATION_EXPANSION,
            hop_budget=DEFAULT_HOP_BUDGET,
            rows=intent.limit,
            scopes=tuple(scopes),
            target_time=kept("target_time", intent.target_time),
            belief_time=kept("belief_time", intent.belief_time),
            window_start=kept("window_start", intent.window_start),
            window_end=kept("window_end", intent.window_end),
            scope=intent.scope,
        )


def plan_retrieval(intent: Intent, read_set: ReadSet, *, planner: Planner | None = None) -> Plan:
    """Plan one retrieval: the entry point the ``query`` path calls.

    ``planner`` defaults to the rule-based one, so the seam exists and is unused. It is a parameter rather than a
    module global so a test can substitute a recorder or a model without patching module state.
    """
    return (planner if planner is not None else RuleBasedPlanner()).plan(intent, read_scopes(read_set))


#: How a plan reaches ``E-036``. The default is a no-op: nothing is logged or stored unless a caller asks. A plan
#: holds scope paths and ids, so it must not be written to ``audit_event.details`` (``RL-072``: no locator in an audit
#: detail), which is why this is a callable the handler owns rather than an audit row.
PlanRecorder = Callable[[Plan], None]


def record_nothing(_plan: Plan) -> None:
    """The default recorder: recording is opt-in, so an unnoticed plan cannot become a hidden side channel."""
    return None


#: The stage boundary ``E-036`` measures: the executor enters ``observe(name)`` around each stage, in plan order. The
#: default is :func:`observe_nothing`. A context manager rather than a pair of calls, so a stage that raises still
#: closes its boundary and a timer cannot leak into the next stage's bucket.
StageObserver = Callable[[str], AbstractContextManager[None]]


def observe_nothing(_stage: str) -> AbstractContextManager[None]:
    """The default stage observer: no measurement (``E-036`` supplies a real one)."""
    return nullcontext()


__all__ = [
    "MODES",
    "PLAN_INTENT_REFUSED",
    "PROCEDURE_APPLICABILITY",
    "STAGES",
    "TEMPORAL_INDEX",
    "Intent",
    "Plan",
    "PlanRecorder",
    "Planner",
    "PlanningError",
    "ReadScope",
    "Route",
    "RuleBasedPlanner",
    "StageObserver",
    "plan_retrieval",
    "observe_nothing",
    "read_scopes",
    "record_nothing",
]
