"""Per-stage retrieval cost records, monotonic measurement and reporting (E-036; ``ARCHITECTURE_V2.md`` §9).

Retrieval executes in five named stages fixed by ``E-031`` (:data:`custom_memory_engine.engine.planning.STAGES`):

1. ``plan`` — pure rule-based planning. Counts ``scope_selectors`` (authorized selectors in → planned scopes out).
2. ``candidate_generation`` — SQL temporal + typed + read fetch. Counts ``rows`` (row ceiling in → fetched rows out).
3. ``expansion`` — association traversal. Counts ``rows`` (seed rows in → seed + reached rows out).
4. ``filter_rerank`` — narrowing, defence-in-depth read re-check, procedure applicability and metadata load. Counts
   ``rows`` (rows in → surviving rows out).
5. ``assemble`` — four-channel routing, bounds, provenance, token budgets and procedure ordering. Counts ``items``
   (``AssemblyCost.candidates_considered`` in → ``AssemblyCost.items_rendered`` out, or audit rows on the audit route)
   and carries ``AssemblyCost.tokens_considered`` / ``tokens_rendered``.

**Single clock, single unit.** All stage latencies are measured in milliseconds (``latency_ms: float``) using one
injected monotonic clock defaulting to :func:`time.perf_counter`. Handlers never call :func:`time.time` directly: they
enter :meth:`StageCollector.track` at each plan boundary so stage attribution cannot drift or leak across boundaries.

**Honest ``Usage`` separation.** Current staged retrieval makes zero charged model or embedding calls. Context token
counts from ``assemble`` (``tokens_considered`` / ``tokens_rendered``) are assembly budget quantities, not provider
billing tokens. :class:`QueryCost` enforces that ``charged_calls == 0`` requires zero charged token counts and
``cost_usd == Decimal(0)``, so ``cost_usd`` can never be fabricated for uncharged local work.

**Process-local reporting.** :class:`CostStore` holds a bounded, time-limited, principal-isolated ring of recent
:class:`QueryCost` entries in memory (no per-query database write amplification) to serve ``cost(ctx, window)``.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Literal

from custom_memory_engine.engine.planning import STAGES, PlanMode

#: What ``candidates_in`` and ``candidates_out`` count for each stage in :data:`STAGES`.
STAGE_UNITS: Mapping[str, str] = {
    "plan": "scope_selectors",
    "candidate_generation": "rows",
    "expansion": "rows",
    "filter_rerank": "rows",
    "assemble": "items",
}
assert tuple(STAGE_UNITS) == STAGES, "STAGE_UNITS must match planning.STAGES exactly"

type CostOperation = Literal["query", "refine"]

#: Bounded process-local retention for ``cost(ctx, window)``: mirrors ``api/refine.py::ResultStore`` (no DB writes).
MAX_COST_RECORDS = 1024
COST_RETENTION = timedelta(hours=1)


class CostError(ValueError):
    """Static code; never echoes memory content, identifiers or scope paths."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _require_non_negative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CostError(code)
    return value


def _require_non_negative_float(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CostError(code)
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise CostError(code)
    return numeric


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile matching ``scripts/bench_retrieval.py::percentile``."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[position]


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


@dataclass(frozen=True, slots=True)
class StageCost:
    """Immutable cost record for one retrieval stage in :data:`planning.STAGES`.

    ``latency_ms`` is wall duration measured on a monotonic clock in milliseconds. ``candidates_in`` and
    ``candidates_out`` are counted in ``unit`` (documented in :data:`STAGE_UNITS`). ``tokens_considered`` and
    ``tokens_rendered`` are context-budget tokens (populated by ``assemble`` from ``AssemblyCost``; zero on stages
    that do not count tokens).
    """

    stage: str
    unit: str
    latency_ms: float
    candidates_in: int
    candidates_out: int
    tokens_considered: int = 0
    tokens_rendered: int = 0

    def __post_init__(self) -> None:
        if self.stage not in STAGE_UNITS:
            raise CostError("unknown_stage")
        if self.unit != STAGE_UNITS[self.stage]:
            raise CostError("stage_unit_mismatch")
        _require_non_negative_float(self.latency_ms, "latency_must_be_nonnegative_finite")
        _require_non_negative_int(self.candidates_in, "candidate_count_must_be_nonnegative_int")
        _require_non_negative_int(self.candidates_out, "candidate_count_must_be_nonnegative_int")
        _require_non_negative_int(self.tokens_considered, "token_count_must_be_nonnegative_int")
        _require_non_negative_int(self.tokens_rendered, "token_count_must_be_nonnegative_int")

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "unit": self.unit,
            "latency_ms": self.latency_ms,
            "candidates_in": self.candidates_in,
            "candidates_out": self.candidates_out,
            "tokens_considered": self.tokens_considered,
            "tokens_rendered": self.tokens_rendered,
        }


@dataclass(frozen=True, slots=True)
class QueryCost:
    """Immutable per-stage cost record for one ``query`` or ``refine`` execution.

    ``stages`` is always in exact :data:`planning.STAGES` order. ``mode`` and ``hop_budget`` come from the executed
    :class:`~custom_memory_engine.engine.planning.Plan` so the record names the depth that actually ran (``V2-D79``).
    Charged provider usage fields (``input_tokens``, ``output_tokens``, ``embedding_tokens``, ``cost_usd``) are
    structurally constrained to zero whenever ``charged_calls == 0``.
    """

    operation: CostOperation
    mode: PlanMode
    hop_budget: int
    stages: tuple[StageCost, ...]
    charged_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    cost_usd: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.operation not in ("query", "refine"):
            raise CostError("cost_operation_invalid")
        _require_non_negative_int(self.hop_budget, "hop_budget_must_be_nonnegative_int")
        if tuple(entry.stage for entry in self.stages) != STAGES:
            raise CostError("stages_must_match_plan_stages")
        _require_non_negative_int(self.charged_calls, "charged_calls_must_be_nonnegative_int")
        _require_non_negative_int(self.input_tokens, "token_count_must_be_nonnegative_int")
        _require_non_negative_int(self.output_tokens, "token_count_must_be_nonnegative_int")
        _require_non_negative_int(self.embedding_tokens, "token_count_must_be_nonnegative_int")
        if not isinstance(self.cost_usd, Decimal) or not self.cost_usd.is_finite() or self.cost_usd < Decimal(0):
            raise CostError("cost_usd_must_be_nonnegative_decimal")
        if self.charged_calls == 0 and (
            self.input_tokens != 0
            or self.output_tokens != 0
            or self.embedding_tokens != 0
            or self.cost_usd != Decimal(0)
        ):
            raise CostError("fabricated_charged_cost")

    @property
    def total_latency_ms(self) -> float:
        return sum(entry.latency_ms for entry in self.stages)

    @property
    def tokens_considered(self) -> int:
        return sum(entry.tokens_considered for entry in self.stages)

    @property
    def tokens_rendered(self) -> int:
        return sum(entry.tokens_rendered for entry in self.stages)

    def stage(self, name: str) -> StageCost:
        """Return the :class:`StageCost` for ``name``, or raise :class:`CostError`."""
        for entry in self.stages:
            if entry.stage == name:
                return entry
        raise CostError("unknown_stage")

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "mode": self.mode,
            "hop_budget": self.hop_budget,
            "total_latency_ms": self.total_latency_ms,
            "tokens_considered": self.tokens_considered,
            "tokens_rendered": self.tokens_rendered,
            "charged_calls": self.charged_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "embedding_tokens": self.embedding_tokens,
            "cost_usd": str(self.cost_usd),
            "stages": [entry.to_dict() for entry in self.stages],
        }


class StageSpan:
    """Mutable count accumulator active only while inside one :meth:`StageCollector.track` block."""

    __slots__ = ("candidates_in", "candidates_out", "tokens_considered", "tokens_rendered")

    def __init__(self) -> None:
        self.candidates_in = 0
        self.candidates_out = 0
        self.tokens_considered = 0
        self.tokens_rendered = 0


class StageCollector:
    """Single monotonic-clock collector for one retrieval execution.

    Every stage boundary enters :meth:`track`; exiting freezes an immutable :class:`StageCost`. Stages must be
    entered at most once and :meth:`finish` requires all five stages of :data:`planning.STAGES`.
    """

    def __init__(self, *, clock: Callable[[], float] = perf_counter) -> None:
        self._clock = clock
        self._recorded: dict[str, StageCost] = {}

    @contextmanager
    def track(self, stage: str) -> Iterator[StageSpan]:
        if stage not in STAGE_UNITS:
            raise CostError("unknown_stage")
        if stage in self._recorded:
            raise CostError("stage_already_recorded")
        span = StageSpan()
        started = self._clock()
        try:
            yield span
        finally:
            elapsed_ms = max(0.0, (self._clock() - started) * 1000.0)
            self._recorded[stage] = StageCost(
                stage=stage,
                unit=STAGE_UNITS[stage],
                latency_ms=elapsed_ms,
                candidates_in=span.candidates_in,
                candidates_out=span.candidates_out,
                tokens_considered=span.tokens_considered,
                tokens_rendered=span.tokens_rendered,
            )

    def record_bypassed(self, stage: str, *, candidates_in: int = 0, candidates_out: int = 0) -> None:
        """Record a stage that ran outside this execution (e.g. ``plan`` during a direct ``execute_plan`` replay)."""
        if stage not in STAGE_UNITS:
            raise CostError("unknown_stage")
        if stage in self._recorded:
            raise CostError("stage_already_recorded")
        self._recorded[stage] = StageCost(
            stage=stage,
            unit=STAGE_UNITS[stage],
            latency_ms=0.0,
            candidates_in=candidates_in,
            candidates_out=candidates_out,
        )

    def has_stage(self, stage: str) -> bool:
        return stage in self._recorded

    def finish(self, *, operation: CostOperation, mode: PlanMode, hop_budget: int) -> QueryCost:
        missing = [name for name in STAGES if name not in self._recorded]
        if missing:
            raise CostError("stage_missing")
        return QueryCost(
            operation=operation,
            mode=mode,
            hop_budget=hop_budget,
            stages=tuple(self._recorded[name] for name in STAGES),
        )


@dataclass(frozen=True, slots=True)
class StageAggregate:
    """Windowed aggregate for one retrieval stage in :class:`CostReport`."""

    stage: str
    unit: str
    calls: int
    total_latency_ms: float
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    candidates_in: int
    candidates_out: int
    tokens_considered: int
    tokens_rendered: int

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "unit": self.unit,
            "calls": self.calls,
            "total_latency_ms": self.total_latency_ms,
            "mean_latency_ms": self.mean_latency_ms,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "candidates_in": self.candidates_in,
            "candidates_out": self.candidates_out,
            "tokens_considered": self.tokens_considered,
            "tokens_rendered": self.tokens_rendered,
        }


@dataclass(frozen=True, slots=True)
class CostReport:
    """Caller-scoped cost report returned by ``cost(ctx, window)`` (``API_V2.md`` §3 row 29, §4.11).

    Contains only latency, candidate counts, context token counts and charged usage for the authenticated caller's
    own queries in ``[window_start, window_end]``. Never exposes another principal's counts or any memory content.

    ``report_scope`` says what the numbers cover: the records live in this process (``V2-D83``, ``T-47``), so a report
    describes this process's recent queries, not a ledger. A restart, a second replica or a second worker starts a new,
    empty one. It is a constant, never an input: no caller can enlarge the scope it is told about.
    """

    principal: str
    window_start: datetime
    window_end: datetime
    query_count: int
    total_latency_ms: float
    tokens_considered: int
    tokens_rendered: int
    charged_calls: int
    input_tokens: int
    output_tokens: int
    embedding_tokens: int
    cost_usd: Decimal
    stages: tuple[StageAggregate, ...]
    #: What the counts cover. Always ``"process"``: the store is process-local (``V2-D83``), so this is a constant
    #: statement about the report's scope and never a caller-controlled value.
    report_scope: Literal["process"] = "process"

    def stage(self, name: str) -> StageAggregate:
        for entry in self.stages:
            if entry.stage == name:
                return entry
        raise CostError("unknown_stage")

    def to_dict(self) -> dict[str, object]:
        return {
            "principal": self.principal,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "query_count": self.query_count,
            "total_latency_ms": self.total_latency_ms,
            "tokens_considered": self.tokens_considered,
            "tokens_rendered": self.tokens_rendered,
            "charged_calls": self.charged_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "embedding_tokens": self.embedding_tokens,
            "cost_usd": str(self.cost_usd),
            "report_scope": self.report_scope,
            "stages": [entry.to_dict() for entry in self.stages],
        }


@dataclass(frozen=True, slots=True)
class _RecordedCost:
    principal: str
    recorded_at: datetime
    cost: QueryCost


def utc_now() -> datetime:
    """Default wall clock for windowed cost retention; injected so tests control windows deterministically."""
    return datetime.now(UTC)


def _parse_aware_instant(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise CostError("window_invalid") from None
    else:
        raise CostError("window_invalid")
    if parsed.tzinfo is None:
        raise CostError("window_invalid")
    return parsed.astimezone(UTC)


def parse_window(
    value: object, *, now: datetime, default_retention: timedelta = COST_RETENTION
) -> tuple[datetime, datetime]:
    """Parse ``cost``'s ``window`` argument into ``(window_start, window_end)`` in UTC.

    Accepted forms:

    * ``None`` — the store's default retention window ``[now - default_retention, now]``;
    * positive ``int`` or ``float`` (seconds) — ``[now - timedelta(seconds=value), now]``;
    * positive ``timedelta`` — ``[now - value, now]``;
    * mapping with aware ``start`` and ``end`` instants (``datetime`` or ISO-8601 text), where ``start < end``.
    """
    if value is None:
        return now - default_retention, now
    if isinstance(value, bool):
        raise CostError("window_invalid")
    if isinstance(value, int | float):
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0.0:
            raise CostError("window_invalid")
        try:
            return now - timedelta(seconds=seconds), now
        except OverflowError:
            raise CostError("window_invalid") from None
    if isinstance(value, timedelta):
        if value <= timedelta(0):
            raise CostError("window_invalid")
        return now - value, now
    if isinstance(value, Mapping):
        if set(value.keys()) != {"start", "end"}:
            raise CostError("window_invalid")
        start = _parse_aware_instant(value["start"])
        end = _parse_aware_instant(value["end"])
        if start >= end:
            raise CostError("window_invalid")
        return start, end
    raise CostError("window_invalid")


class CostStore:
    """Bounded, process-local store of per-query cost records backing ``cost(ctx, window)``.

    Not persisted in PostgreSQL on purpose: writing one or five rows per read query would turn read-only retrieval
    into write-amplifying transactions. Records are isolated by principal and contain zero memory content or scopes.
    """

    def __init__(
        self,
        *,
        retention: timedelta = COST_RETENTION,
        capacity: int = MAX_COST_RECORDS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not isinstance(retention, timedelta) or retention <= timedelta(0):
            raise CostError("cost_retention_must_be_positive")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise CostError("cost_capacity_must_be_positive_int")
        self._retention = retention
        self._capacity = capacity
        self._clock = clock
        self._records: OrderedDict[str, list[_RecordedCost]] = OrderedDict()

    def __len__(self) -> int:
        return sum(len(records) for records in self._records.values())

    def record(self, principal: str, cost: QueryCost) -> None:
        """Append one immutable :class:`QueryCost` for ``principal``, evicting expired and overflow entries."""
        now = self._now()
        self._drop_expired(now)
        records = self._records.setdefault(principal, [])
        self._records.move_to_end(principal)
        while len(self._records) > self._capacity:
            self._records.popitem(last=False)
        records.append(_RecordedCost(principal=principal, recorded_at=now, cost=cost))
        self._make_room(principal)

    def report(self, principal: str, *, window: object = None) -> CostReport:
        """Aggregate ``principal``'s own recorded queries within ``window`` into an immutable :class:`CostReport`."""
        now = self._now()
        self._drop_expired(now)
        if self._records.get(principal):
            # a principal reading its own report is in use, so it is not the idle one evicted next (`T-47`). A report
            # never creates a bucket: reading cannot make the store grow.
            self._records.move_to_end(principal)
        window_start, window_end = parse_window(window, now=now, default_retention=self._retention)
        matched = [
            entry.cost for entry in self._records.get(principal, []) if window_start <= entry.recorded_at <= window_end
        ]
        stage_aggregates: list[StageAggregate] = []
        for stage_name in STAGES:
            items = [entry.stage(stage_name) for entry in matched]
            latencies = [item.latency_ms for item in items]
            total = sum(latencies)
            count = len(items)
            stage_aggregates.append(
                StageAggregate(
                    stage=stage_name,
                    unit=STAGE_UNITS[stage_name],
                    calls=count,
                    total_latency_ms=total,
                    mean_latency_ms=(total / count) if count else 0.0,
                    p50_latency_ms=_median(latencies),
                    p95_latency_ms=_percentile(latencies, 0.95),
                    candidates_in=sum(item.candidates_in for item in items),
                    candidates_out=sum(item.candidates_out for item in items),
                    tokens_considered=sum(item.tokens_considered for item in items),
                    tokens_rendered=sum(item.tokens_rendered for item in items),
                )
            )
        return CostReport(
            principal=principal,
            window_start=window_start,
            window_end=window_end,
            query_count=len(matched),
            total_latency_ms=sum(entry.total_latency_ms for entry in matched),
            tokens_considered=sum(entry.tokens_considered for entry in matched),
            tokens_rendered=sum(entry.tokens_rendered for entry in matched),
            charged_calls=sum(entry.charged_calls for entry in matched),
            input_tokens=sum(entry.input_tokens for entry in matched),
            output_tokens=sum(entry.output_tokens for entry in matched),
            embedding_tokens=sum(entry.embedding_tokens for entry in matched),
            cost_usd=sum((entry.cost_usd for entry in matched), Decimal(0)),
            stages=tuple(stage_aggregates),
        )

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise CostError("cost_clock_must_be_timezone_aware")
        return now.astimezone(UTC)

    def _drop_expired(self, now: datetime) -> None:
        cutoff = now - self._retention
        for principal, records in list(self._records.items()):
            self._records[principal] = [entry for entry in records if entry.recorded_at >= cutoff]
            if not self._records[principal]:
                del self._records[principal]

    def _make_room(self, active_principal: str) -> None:
        while len(self) > self._capacity:
            active_records = self._records[active_principal]
            if len(active_records) > 1:
                del active_records[0]
                continue
            surplus = next(
                ((principal, records) for principal, records in self._records.items() if len(records) > 1),
                None,
            )
            if surplus is not None:
                _, records = surplus
                del records[0]
                continue
            self._records.popitem(last=False)


__all__ = [
    "COST_RETENTION",
    "MAX_COST_RECORDS",
    "STAGE_UNITS",
    "CostError",
    "CostOperation",
    "CostReport",
    "CostStore",
    "QueryCost",
    "StageAggregate",
    "StageCollector",
    "StageCost",
    "StageSpan",
    "parse_window",
    "utc_now",
]
