"""Read-path handlers for ``get``, ``query`` and ``provenance``.

``API_V2.md`` §4.2 defines ``get`` (one artifact by id) and ``query`` (staged retrieval returning the four channels
of ``CHANNELS.md``). Both are owned by ``E-033``; that task is gated behind ``G2``, and the Project Owner authorized
this minimal landing early under ``V2-D66`` so the six coding-profile fixtures can be stored **and read back** through
the generic public API (issue #136 acceptance criteria 4 and 5).

**Temporal modes (E-031, V2-D73).** ``query`` now accepts a ``mode`` in ``QueryRequest``:

* ``current_state`` (default) — the view an observer has now;
* ``historical_as_of`` — the view that existed at ``target_time``, as believed now;
* ``historical_window`` — versions whose event range overlaps ``[window_start, window_end)``;
* ``audit_as_of`` — the recorded-range slice at ``belief_time`` (Tier C only; never answer-context material;
  ``TECHNICAL_SPECIFICATION.md`` 3.5).

The reference instant ρ is the engine's own clock (``engine.predicates.reference_instant``), never the caller's: a
request carrying ``reference_time`` is refused (``V2-D73``, ``I4``).

``change`` (``V2-D81``) diffs two of those predicates: the belief in effect at ``window_start`` against the belief in
effect at ``window_end``, both under ρ. Its result carries ``changes`` beside the channels, and the union of the two
views is routed through the same assembler, so a changed artifact is rendered exactly as a single-view query renders
it. Every temporal read routes through :func:`engine.predicates.fetch_scoped_rows`, which applies the temporal
predicate and the caller's read predicate in **one** statement, then feeds the same channel-classification pipeline.

**Staged retrieval (E-031, ``V2-D11``).** ``query`` now splits into the two halves ``ARCHITECTURE_V2.md`` §9 draws:
:func:`~.engine.planning.plan_retrieval` is a **pure** function producing a
:class:`~.engine.planning.Plan`, and :meth:`ReadHandlers.execute_plan` runs it. The plan names the index, the ordered
filters, the expansion intent, the route (``channels`` or ``audit``) and the five stage names ``E-036`` measures; it
names **no** channel membership or ranking. The request limit bounds candidate generation. E-032 follows associations
from those candidates and filters each reached row as it enters expansion (``V2-D76``).

**Assembly (E-033).** Query results now pass through the pure four-channel assembler, with per-channel token budgets,
reported refusals/truncation, coverage and assembly cost. Each returned artifact carries a provenance bundle.
``get`` returns its bundle; ``provenance`` traverses readable lineage to a bounded depth and stops at opaque sources.
``change`` returns :attr:`QueryResult.changes` (``V2-D81``). Per-stage collection beyond assembly is ``E-036``'s
responsibility.

**Iterative refine (E-034).** ``query`` returns an opaque ``result_id`` and ``refine`` drills into that result with
one rule-based operator (``artifact``/``channel``/``mention``/``scope``). The handle records the issuing read set, and
a refine executes under it — expansion cut, candidates re-read from the database, the instruction applied before
assembly — so a drill-down can narrow and never widen (``V2-D83``).

**Why nothing below ``Engine.call`` skips authorisation.** The caller arrives already authorised for the *operation*;
what decides which **rows** are visible is row-level security, bound to the authenticated principal by
``Runtime.session``. ``scope.access.read_predicate_named`` narrows further to the caller's resolved read set, in the
same statement as the temporal predicate, and ``row_visible`` re-checks every returned row as defence in depth
(``V2-D45``). ``fetch_scoped_rows`` also verifies that ``app.user_id`` is the query's user before reading.

**``audit_as_of`` Tier-C enforcement.** ``API_V2.md`` §4.2 says ``audit_as_of`` is Tier C only and never returns
answer-context material. A Tier B caller therefore gets ``forbidden`` before any row is read, and the rows come back
in ``QueryResult.audit`` with ``is_audit=True`` — never in an answer channel, because ``channel_of`` maps a retired
artifact to ``none`` and would drop exactly the withdrawn rows an audit exists to show.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from typing import cast

import structlog
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.refine import (
    MAX_REFINE_DEPTH,
    InstructionError,
    Narrowing,
    ResultStore,
    parse_instruction,
)
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.engine.change import Change, diff_views
from custom_memory_engine.engine.channels import (
    ArtifactView,
    AssemblyCost,
    Bound,
    BoundedFact,
    Channel,
    ChannelBudgets,
    ChannelCoverage,
    ChannelError,
    ConflictSide,
    CoverageReport,
    Verification,
    assemble,
    channel_of,
    stale_of,
)
from custom_memory_engine.engine.cost import (
    CostError,
    CostOperation,
    CostReport,
    CostStore,
    QueryCost,
    StageCollector,
    StageSpan,
)
from custom_memory_engine.engine.expansion import expand_associations
from custom_memory_engine.engine.hybrid import (
    JUDGE_CANDIDATES,
    JUDGE_RELEVANCE_FLOOR,
    Recall,
    RecallError,
    RecallQuery,
    RelevanceJudge,
    hybrid_recall,
    slot_for_dimension,
)
from custom_memory_engine.engine.outcomes import stats_for as outcome_stats_for
from custom_memory_engine.engine.planning import (
    ASSOCIATION_EXPANSION,
    CHANGE_MODE,
    HYBRID_RECALL,
    MAX_QUERY_TEXT,
    PROCEDURE_APPLICABILITY,
    Intent,
    Plan,
    PlanMode,
    Planner,
    PlanningError,
    PlanRecorder,
    RuleBasedPlanner,
    StageObserver,
    observe_nothing,
    plan_retrieval,
    query_digest,
    record_nothing,
    refine_plan,
)
from custom_memory_engine.engine.predicates import (
    ScopedRow,
    TemporalQuery,
    TemporalQueryError,
    audit_as_of,
    current_state,
    fetch_scoped_rows,
    historical_as_of,
    historical_window,
    reference_instant,
)
from custom_memory_engine.engine.procedure_matching import ApplicabilityRequest, ArtifactFacts, applicable
from custom_memory_engine.engine.procedure_ranking import order_procedures
from custom_memory_engine.engine.procedure_shape import NO_SHAPE, Precondition, ProcedureShape, shapes_for
from custom_memory_engine.engine.provenance import (
    DEFAULT_PROVENANCE_DEPTH,
    MAX_PROVENANCE_DEPTH,
    ProvenanceView,
    provenance_view,
    sources_for_artifacts,
)
from custom_memory_engine.engine.staleness import ClosureCache, staleness_inputs
from custom_memory_engine.engine.statistics import ArtifactStats, StatsError, read_stats
from custom_memory_engine.model.axes import Kind, Lifecycle
from custom_memory_engine.scope.access import (
    ReadEntry,
    ReadSet,
    SelectorMode,
    Visibility,
    covers,
    read_predicate_named,
    row_visible,
)
from custom_memory_engine.scope.paths import ScopeError, parse_scope
from custom_memory_engine.scope.provenance import ProvenanceReference, ProvenanceSource, opaque_handle
from custom_memory_engine.semantic.contracts import Embedder

logger = structlog.get_logger(__name__)

#: ``API_V2.md`` §4.2 declares ``resource_limit`` for "more than 16 selectors".
MAX_SELECTORS = 16
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
#: The four single-predicate modes (``E-031``); ``change`` is the fifth and is a diff of two of them (``V2-D81``).
TEMPORAL_MODES = frozenset({"current_state", "historical_as_of", "historical_window", "audit_as_of"})
_ALL_VISIBILITIES: frozenset[Visibility] = frozenset({"private", "project", "shared"})


class Artifact:
    """One stored artifact returned by ``get`` or ``query``, with the query's association hop distance."""

    __slots__ = (
        "artifact_id", "bound", "channel", "conflict_sides", "content", "hop_distance", "kind", "label",
        "lifecycle", "preconditions", "provenance", "scope_path", "steps", "support_summary", "verification",
    )  # fmt: skip

    def __init__(
        self,
        *,
        artifact_id: str,
        kind: str,
        lifecycle: str,
        content: str,
        scope_path: str,
        channel: Channel,
        verification: str,
        shape: ProcedureShape | None = None,
        hop_distance: int = 0,
        provenance: tuple[ProvenanceReference, ...] = (),
        label: str | None = None,
        support_summary: str = "",
        bound: Bound | None = None,
        conflict_sides: tuple[ConflictSide, ...] = (),
    ) -> None:
        self.artifact_id = artifact_id
        self.kind = kind
        self.lifecycle = lifecycle
        self.content = content
        self.scope_path = scope_path
        self.channel = channel
        self.verification = verification
        self.hop_distance = hop_distance
        self.provenance = provenance
        self.label = label
        self.support_summary = support_summary
        self.bound = bound
        self.conflict_sides = conflict_sides
        # E-030: a procedure's preconditions and steps (empty when stored without them); None on every other kind
        self.preconditions: tuple[Precondition, ...] | None = None if shape is None else shape.preconditions
        self.steps: tuple[str, ...] | None = None if shape is None else shape.steps

    def _key(self) -> tuple[object, ...]:
        return (
            self.artifact_id,
            self.kind,
            self.lifecycle,
            self.content,
            self.scope_path,
            self.channel,
            self.verification,
            self.preconditions,
            self.steps,
            self.hop_distance,
            self.provenance,
            self.label,
            self.support_summary,
            self.bound,
            self.conflict_sides,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Artifact):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __repr__(self) -> str:
        return f"Artifact({self.artifact_id!r}, kind={self.kind!r}, channel={self.channel!r})"


@dataclass(frozen=True, slots=True)
class Judgement:
    """What the relevance judge said about one page: a reported fact, like :attr:`QueryResult.arms`.

    ``scored`` is how many candidates the judge read, ``kept`` how many cleared its floor, and ``best`` the highest
    score it returned (``None`` when it read nothing). ``model`` and ``floor`` are recorded because a score means
    nothing without the weights and the operating point that produced it.
    """

    model: str
    floor: float
    scored: int
    kept: int
    best: float | None


class QueryResult:
    """The four channels, explicit per-channel coverage and assembly cost (``API_V2.md`` §4.2).

    ``is_audit`` is ``True`` only for ``audit_as_of`` queries; ``TECHNICAL_SPECIFICATION.md`` 3.5 forbids
    using those results as answer context, so callers must check this flag before treating the result as one.

    ``changes`` is non-empty only for ``mode='change'`` (``V2-D81``): one entry per key that differs between the two
    views, alongside the channels built from their union. A caller reads *what changed* from ``changes`` and *what it
    is* from the channels, which is why the diff names ids rather than carrying a second copy of the content.

    ``result_id`` is the opaque handle a ``refine`` drills into (``E-034``, ``V2-D83``). It is ``None`` only on a
    result built outside the answer path (a plain ``execute_plan`` replay, or an empty shortcut); ``query`` and
    ``refine`` always return one. It is not part of equality: two results with equal content are still equal results,
    and a handle is a capability, not content.
    """

    __slots__ = (
        "arms", "audit", "changes", "conflicts", "coverage", "cost", "facts", "hypotheses", "is_audit", "judgement",
        "procedures", "refused", "result_id", "stage_costs",
    )  # fmt: skip

    def __init__(
        self,
        *,
        facts: Sequence[Artifact] = (),
        hypotheses: Sequence[Artifact] = (),
        procedures: Sequence[Artifact] = (),
        conflicts: Sequence[Artifact] = (),
        refused: Sequence[tuple[str, str]] = (),
        audit: Sequence[Artifact] = (),
        is_audit: bool = False,
        coverage: CoverageReport | None = None,
        cost: AssemblyCost | None = None,
        changes: Sequence[Change] = (),
        result_id: str | None = None,
        stage_costs: QueryCost | None = None,
        arms: Sequence[str] = (),
        judgement: Judgement | None = None,
    ) -> None:
        self.facts = tuple(facts)
        self.hypotheses = tuple(hypotheses)
        self.procedures = tuple(procedures)
        self.conflicts = tuple(conflicts)
        self.refused = tuple(refused)
        self.audit = tuple(audit)
        self.changes = tuple(changes)
        self.is_audit = is_audit
        self.result_id = result_id
        self.stage_costs = stage_costs
        # Which ranking arms actually ran (`dense`, `lexical`, `trigram`), in a fixed order; empty when the plan was
        # the recency scan or the answer was a shortcut. It exists so "was this answer ranked, and was the dense arm
        # part of it" is a *reported fact* rather than an inference from a latency number: a query whose embedding
        # was unavailable answers from the lexical arms and says so here (V2-D98).
        self.arms = tuple(arms)
        # What the relevance judge said, or `None` when no judge is configured (V2-D104). `None` and "judged and
        # nothing cleared the floor" are different answers — the second one is an empty page and is *reported*, so a
        # caller can tell "this deployment cannot judge" from "this question has no answer here".
        self.judgement = judgement
        empty = ChannelCoverage(0, 0, (), 0, 0, 0, 0)
        self.coverage = coverage or CoverageReport(empty, empty, empty, empty)
        self.cost = cost or AssemblyCost(0, 0, 0, 0)

    def channel(self, name: str) -> tuple[Artifact, ...]:
        """The items in one channel, by the ``CHANNELS.md`` name."""
        if name not in {"facts", "hypotheses", "procedures", "conflicts"}:
            raise ValueError("unknown_channel")
        return cast(tuple[Artifact, ...], getattr(self, name))

    @property
    def provenance_bundle(self) -> tuple[tuple[str, tuple[ProvenanceReference, ...]], ...]:
        """Per-artifact provenance in channel order; audit rows never enter answer-context provenance."""
        return tuple(
            (artifact.artifact_id, artifact.provenance)
            for name in ("facts", "hypotheses", "procedures", "conflicts")
            for artifact in self.channel(name)
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, QueryResult):
            return NotImplemented
        return (
            self.facts,
            self.hypotheses,
            self.procedures,
            self.conflicts,
            self.changes,
            self.refused,
            self.audit,
            self.is_audit,
            self.coverage,
            self.cost,
        ) == (
            other.facts,
            other.hypotheses,
            other.procedures,
            other.conflicts,
            other.changes,
            other.refused,
            other.audit,
            other.is_audit,
            other.coverage,
            other.cost,
        )

    def __repr__(self) -> str:
        audited = f", audit={len(self.audit)}" if self.is_audit else ""
        changed = f", changes={len(self.changes)}" if self.changes else ""
        return (
            f"QueryResult(facts={len(self.facts)}, hypotheses={len(self.hypotheses)}, "
            f"procedures={len(self.procedures)}, conflicts={len(self.conflicts)}, "
            f"refused={len(self.refused)}{audited}{changed})"
        )


def _channel_for(
    kind: str, lifecycle: str, verification: str, open_contradiction: bool, stale: bool
) -> Channel | None:
    """The channel of one stored row, or ``None`` when the tuple is not legal (reported, never rendered).

    ``stale`` is **derived** (``V2-D24``, ``engine/staleness.py``), never read from a column and never defaulted to
    fresh: a verified abstraction whose closure, ledger, sources, method or expiry no longer hold renders in
    ``hypotheses`` (``CHANNELS.md`` C-P3). It is a required argument so no call site can forget it.
    """
    try:
        return channel_of(
            cast(Kind, kind),
            cast(Lifecycle, lifecycle),
            cast(Verification, verification),
            stale,
            open_contradiction,
        )
    except ChannelError:
        return None


def _text_or_none(value: object) -> str | None:
    """A request's ``text`` as ``str``, or ``None`` when absent. Non-string values are a type error, not a coercion."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise EntryError("validation_error", "text_invalid")
    return value


def _result_ids(result: QueryResult) -> tuple[str, ...]:
    """Every artifact id a result named, in channel order, audit included.

    This is the whole of the candidate set a refine may return: a drill-down is a subset of the prior answer, never a
    fresh page of the read set (``E-034``).
    """
    return tuple(
        item.artifact_id
        for items in (result.facts, result.hypotheses, result.procedures, result.conflicts, result.audit)
        for item in items
    )


def _channel_name(
    row: ScopedRow,
    verifications: Mapping[str, str],
    contradicted: set[str],
    stale: set[str],
) -> str | None:
    """The channel the assembler will route ``row`` to, computed the same way ``assemble`` does (``channel_of``).

    A ``refine`` instruction may name a channel, and which channel an artifact belongs to is never the instruction
    interpreter's decision: it is ``channel_of``'s pure function of the artifact's own tuple (``CHANNELS.md``,
    ``V2-D48``). This helper reads that function with the *same* inputs assembly uses, so a narrowed result cannot
    disagree with the channel the artifact came from.
    """
    verification = _verification_for(row.lifecycle, verifications.get(row.id))
    return _channel_for(row.kind, row.lifecycle, verification, row.id in contradicted, row.id in stale)


def _verification_for(lifecycle: str, recorded: str | None) -> str:
    """The ``verification`` membership input of one stored row (``CHANNELS.md`` §1.1).

    ``abstraction_stats`` is the only home for a verification status and ``guard_abstraction_stats`` refuses a row for
    any kind but ``abstraction``. So a row with no ledger is either non-abstraction or a pre-``abstraction_stats`` row:
    its ``lifecycle`` decides which default is legal. ``inferred`` requires ``unverified`` or ``verified`` (§1.1: an
    inference is never grounded, so it is never ``none``), and every other lifecycle admits ``none``.

    Reading the map with a blanket ``"none"`` default refused **every** inferred episode, claim and procedure as
    ``illegal_artifact_tuple`` -- they vanished from ``hypotheses`` and appeared in ``refused``. The reference model
    (``tests/test_channel_membership_model.py``) routes all of them to ``hypotheses``.
    """
    if recorded is not None:
        return recorded
    return "unverified" if lifecycle == "inferred" else "none"


class ReadHandlers:
    """Read operations bound to one :class:`Runtime`."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        planner: Planner | None = None,
        recorder: PlanRecorder | None = None,
        observer: StageObserver | None = None,
        results: ResultStore | None = None,
        costs: CostStore | None = None,
        clock: Callable[[], float] | None = None,
        collect_cost: bool = True,
        embedder: Embedder | None = None,
        judge: RelevanceJudge | None = None,
        judge_floor: float = JUDGE_RELEVANCE_FLOOR,
    ) -> None:
        self.runtime = runtime
        # E-031: the model seam, defaulted to the rule-based planner and therefore unused (criterion 9). The recorder
        # and observer remain opt-in seams; E-036's stage collector runs alongside the observer so injected stage
        # observers stay compatible and attribution is measured in one place.
        self.planner: Planner = planner if planner is not None else RuleBasedPlanner()
        self.recorder: PlanRecorder = recorder if recorder is not None else record_nothing
        self.observer: StageObserver = observer if observer is not None else observe_nothing
        # E-034: the handle map `query` issues into and `refine` resolves from. The engine owns the canonical store
        # (the entry point resolves a `result_id` before dispatch); a handler built on its own keeps a private one.
        self.results: ResultStore = results if results is not None else ResultStore()
        # E-036: per-stage cost collection and caller-scoped windowed reporting (`cost` operation).
        self.costs: CostStore = costs if costs is not None else CostStore()
        self._clock: Callable[[], float] | None = clock
        self._collect_cost: bool = collect_cost
        # The engine's own query embedder (V2-D98). ``None`` keeps the pre-W3.5 behaviour: text-only queries rank by
        # the two language-agnostic lexical arms, and a caller that wants the dense arm supplies its own vector.
        self.embedder: Embedder | None = embedder
        # The relevance judge (V2-D104): a cross-encoder that reads a question and a memory *together* and answers on
        # one absolute scale, so a floor on its score can refuse to answer. ``None`` keeps the fused order and reports
        # ``judgement=None``; a deployment that sets one gets a gate that an unanswerable question comes back empty
        # from. The floor belongs to the judge, not to the engine: it is a property of the weights, so a deployment
        # that swaps the model must re-derive it (`reports/retrieval/README.md`).
        self.judge: RelevanceJudge | None = judge
        self.judge_floor: float = judge_floor
        # E-038 (V2-D24): the closure-hash cache the read path derives `stale` through. One per handler, so repeated
        # reads of an unchanged abstraction hash its closure once; the deletion token invalidates it (F6).
        self.closure_cache = ClosureCache()

    def _new_collector(self) -> StageCollector | None:
        if not self._collect_cost:
            return None
        return StageCollector(clock=self._clock) if self._clock is not None else StageCollector()

    @contextmanager
    def _stage(self, collector: StageCollector | None, stage: str) -> Iterator[StageSpan | None]:
        """Enter one retrieval stage boundary for ``E-036`` cost measurement and any external ``StageObserver``."""
        if collector is None:
            with self.observer(stage):
                yield None
            return
        with collector.track(stage) as span, self.observer(stage):
            yield span

    def _finalize_cost(
        self, collector: StageCollector | None, *, operation: CostOperation, plan: Plan
    ) -> QueryCost | None:
        if collector is None:
            return None
        stage_costs = collector.finish(operation=operation, mode=plan.mode, hop_budget=plan.hop_budget)
        logger.debug(
            "retrieval_stage_costs",
            operation=stage_costs.operation,
            mode=stage_costs.mode,
            hop_budget=stage_costs.hop_budget,
            total_latency_ms=round(stage_costs.total_latency_ms, 3),
            stages={entry.stage: round(entry.latency_ms, 3) for entry in stage_costs.stages},
        )
        return stage_costs

    # -- shared reads ------------------------------------------------------------------------------------------

    async def _verification_of(self, connection: AsyncConnection, user: str, ids: Sequence[str]) -> dict[str, str]:
        """Recorded verification status of abstractions, from ``ABSTRACTION_STATS`` (``I14``).

        Only an abstraction carries a ledger: ``guard_abstraction_stats`` refuses a row for any other kind, and
        ``verification='none'`` is the only legal value for a grounded artifact (``CHANNELS.md`` §1.1, ``V2-D48``
        C-P1). ``inferred`` is different -- it requires ``unverified`` or ``verified`` -- and **every** kind may be
        ``inferred`` (``axes.LEGAL_LIFECYCLE``), while only an abstraction has a ledger. So the caller must not read
        this map with a blanket ``"none"`` default (see :func:`_verification_for`).
        """
        if not ids:
            return {}
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT version_id, verification_status FROM {t} WHERE user_id = %s AND version_id = ANY(%s)"
                ).format(t=self.runtime.table("abstraction_stats")),
                (user, list(ids)),
            )
            return {str(row[0]): str(row[1]) for row in await cursor.fetchall()}

    async def _bounds_of(
        self, connection: AsyncConnection, user: str, ids: Sequence[str]
    ) -> tuple[dict[str, Bound], dict[str, str]]:
        """Return complete verified-abstraction bounds and their content-free support summaries."""
        if not ids:
            return {}, {}
        bounds: dict[str, Bound] = {}
        summaries: dict[str, str] = {}
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT s.version_id, s.support_count, s.opportunity_count, s.confidence, "
                    "s.coverage, s.confidence_method_id, m.version, s.expires_at, v.verified_on "
                    "FROM {s} AS s "
                    "LEFT JOIN {m} AS m ON m.id = s.confidence_method_id "
                    "LEFT JOIN LATERAL ("
                    "  SELECT verified_on FROM {v} "
                    "  WHERE version_id = s.version_id AND user_id = s.user_id AND outcome = 'verified' "
                    "  ORDER BY verified_on DESC LIMIT 1"
                    ") AS v ON true "
                    "WHERE s.user_id = %s AND s.version_id = ANY(%s)"
                ).format(
                    s=self.runtime.table("abstraction_stats"),
                    m=self.runtime.table("method"),
                    v=self.runtime.table("verification"),
                ),
                (user, list(ids)),
            )
            for (
                artifact_id,
                support,
                opportunity,
                confidence,
                coverage,
                method_id,
                method_version,
                expires,
                verified,
            ) in await cursor.fetchall():
                identifier = str(artifact_id)
                summaries[identifier] = f"{support} supporting observations of {opportunity} opportunities"
                if (
                    confidence is None
                    or method_id is None
                    or method_version is None
                    or expires is None
                    or verified is None
                ):
                    continue
                bounds[identifier] = Bound(
                    support_count=int(support),
                    opportunity_count=int(opportunity),
                    confidence_lower=float(confidence),
                    coverage=float(coverage),
                    method_id=str(method_id),
                    method_version=str(method_version),
                    verified_on=verified.isoformat(),
                    expires_at=expires,
                )
        return bounds, summaries

    async def _facts_of(
        self, connection: AsyncConnection, user: str, rows: Sequence[ScopedRow]
    ) -> tuple[ArtifactFacts, ...]:
        """The retrieved rows as the applicability matcher sees them: kind, lifecycle, facet and predicate.

        ``facet`` (``um_facet``) and ``predicate`` (``predicate_key``) are not on :class:`ScopedRow`, so they are read
        here in one statement, under the same session and RLS as every other per-row fact. Without them an
        ``exists``/``absent`` condition naming either field could never hold through ``query`` (``E-030-F2``).
        """
        if not rows:
            return ()
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT id, um_facet, predicate_key FROM {t} WHERE user_id = %s AND id = ANY(%s)").format(
                    t=self.runtime.table("memory_version")
                ),
                (user, [row.id for row in rows]),
            )
            extra: dict[str, tuple[str | None, str | None]] = {
                str(identifier): (None if facet is None else str(facet), None if predicate is None else str(predicate))
                for identifier, facet, predicate in await cursor.fetchall()
            }
        return tuple(ArtifactFacts(row.kind, row.lifecycle, *extra.get(row.id, (None, None))) for row in rows)

    async def _stale_ids(self, connection: AsyncConnection, user: str, verifications: Mapping[str, str]) -> set[str]:
        """The ids among ``verifications`` that are derived stale now (``V2-D24``; closes ``E-038-F5``).

        Only a ``verified`` artifact can be stale (``CHANNELS.md`` 2.3: an unverified one is already a hypothesis), so
        the derivation runs for those alone and a read with no verified abstraction costs nothing extra. A verified
        artifact with no sealed ``closed_over_hash`` fails closed to stale (``staleness_inputs``), which is the right
        answer: it cannot prove it is fresh.

        **Current state only** (``E-038-F3``). The answer routes use it, including the historical modes, because those
        are answer context *now*: an abstraction the engine now knows to be stale must not be presented as a fact.
        ``audit_as_of`` never calls it — an audit shows a past belief as it was held.
        """
        stale: set[str] = set()
        for artifact_id, verification in sorted(verifications.items()):
            if verification != "verified":
                continue
            inputs = await staleness_inputs(
                connection, user, artifact_id, cache=self.closure_cache, schema_name=self.runtime.schema_name
            )
            if stale_of(inputs):
                stale.add(artifact_id)
        return stale

    async def _contradicted_ids(self, connection: AsyncConnection, user: str, ids: Sequence[str]) -> set[str]:
        """Which of ``ids`` is an endpoint of an open ``contradicts`` edge (``V2-D48`` C-P4)."""
        return set(await self._contradictions(connection, user, ids))

    async def _contradictions(self, connection: AsyncConnection, user: str, ids: Sequence[str]) -> dict[str, set[str]]:
        """Each of ``ids`` that has an open contradiction, mapped to **its own** counterparts (``V2-D48`` C-P4).

        ``open_contradiction`` holds when *both* endpoints are grounded and the edge came from the grounding policy
        or a Tier C caller. The edge records that decision in ``origin``, so this reads a recorded fact rather than
        re-deriving the rule. The map is per artifact (``V2-D80``): an artifact's sides are the endpoints of *its*
        edges, never every other contradicted row the query happened to retrieve (``CHANNELS.md`` R5: never merge).
        """
        if not ids:
            return {}
        wanted = set(ids)
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT DISTINCT a.from_id, a.to_id FROM {a} AS a "
                    "JOIN {v} AS f ON f.id = a.from_id AND f.user_id = a.user_id "
                    "JOIN {v} AS t ON t.id = a.to_id AND t.user_id = a.user_id "
                    "WHERE a.user_id = %s AND a.type = 'contradicts' "
                    "AND a.origin IN ('grounding_policy','tier_c') "
                    "AND f.lifecycle IN ('observed','asserted') AND t.lifecycle IN ('observed','asserted') "
                    "AND (a.from_id = ANY(%s) OR a.to_id = ANY(%s))"
                ).format(a=self.runtime.table("association"), v=self.runtime.table("memory_version")),
                (user, list(ids), list(ids)),
            )
            found: dict[str, set[str]] = {}
            for from_id, to_id in await cursor.fetchall():
                if str(from_id) in wanted:
                    found.setdefault(str(from_id), set()).add(str(to_id))
                if str(to_id) in wanted:
                    found.setdefault(str(to_id), set()).add(str(from_id))
        return found

    async def _conflict_counterparts(
        self,
        connection: AsyncConnection,
        user: str,
        read_set: ReadSet,
        contradictions: Mapping[str, set[str]],
    ) -> dict[str, tuple[ConflictSide, ...]]:
        """The other side of every open contradiction, whether or not the query retrieved it (``V2-D80``).

        ``CHANNELS.md`` §1 makes membership independent of the query, and R5 requires a ``conflicts`` item to show
        both sides, so the counterpart is *loaded*, never assumed to be among the candidates. A counterpart the caller
        may read renders with its id, state and text. One it may not read renders as R6/``V2-D37`` render an
        unreadable source: a per-caller opaque handle and **no** text. Its state is shown because ``C-P4`` already
        fixes it to a grounded lifecycle -- the edge is open only when both endpoints are grounded -- so it discloses
        nothing the existence of the contradiction does not. Visible sides come first by id, then opaque ones by
        handle, so the order never encodes an unreadable id.
        """
        others = sorted({other for counterparts in contradictions.values() for other in counterparts})
        if not others:
            return {}
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT id, lifecycle, content, scope_path, visibility FROM {v} "
                    "WHERE user_id = %s AND id = ANY(%s)"
                ).format(v=self.runtime.table("memory_version")),
                (user, others),
            )
            loaded = {str(row[0]): row[1:] for row in await cursor.fetchall()}
        visible: dict[str, ConflictSide] = {}
        opaque: dict[str, ConflictSide] = {}
        for other, (lifecycle, content, scope_path, visibility) in loaded.items():
            if row_visible(read_set, scope_path, visibility):
                visible[other] = ConflictSide(other, cast(Lifecycle, lifecycle), str(content))
            else:
                handle = opaque_handle(self.runtime.provenance_key, user, other)
                opaque[other] = ConflictSide(handle, cast(Lifecycle, lifecycle), "")
        rendered: dict[str, tuple[ConflictSide, ...]] = {}
        for artifact_id, counterparts in contradictions.items():
            shown = sorted((visible[o] for o in counterparts if o in visible), key=lambda side: side.artifact_id)
            hidden = sorted((opaque[o] for o in counterparts if o in opaque), key=lambda side: side.artifact_id)
            rendered[artifact_id] = (*shown, *hidden)
        return rendered

    # -- operations --------------------------------------------------------------------------------------------

    async def get(self, call: Call) -> Artifact:
        artifact_id = call.arguments.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise EntryError("validation_error", "artifact_id_required")
        user = call.ctx.principal
        async with self.runtime.session(user) as connection, connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT id, kind, lifecycle, content, scope_path, visibility FROM {t} "
                    "WHERE id = %s AND user_id = %s AND kind IS NOT NULL AND lifecycle IS NOT NULL"
                ).format(t=self.runtime.table("memory_version")),
                (artifact_id, user),
            )
            row = await cursor.fetchone()
            if row is None:
                raise EntryError("forbidden")  # absent and unreadable are the same answer (T-11, T-18)
            raw_id, raw_kind, raw_lifecycle, raw_content, raw_scope, raw_visibility = row
            identifier = str(raw_id)
            kind = str(raw_kind)
            lifecycle = str(raw_lifecycle)
            content = str(raw_content)
            scope_path = str(raw_scope)
            if not row_visible(call.ctx.read_set, scope_path, str(raw_visibility)):
                raise EntryError("forbidden")
            verification = _verification_for(
                lifecycle, (await self._verification_of(connection, user, [identifier])).get(identifier)
            )
            contradictions = await self._contradictions(connection, user, [identifier])
            contradicted = identifier in contradictions
            stale = identifier in await self._stale_ids(connection, user, {identifier: verification})
            # Routing comes before loading provenance/bounds: a retired or illegal tuple is not rendered at all
            # (`API_V2.md` §1.1's `forbidden` for a target that cannot be answered), so nothing else is fetched.
            channel = _channel_for(kind, lifecycle, verification, contradicted, stale)
            if channel is None or channel == "none":
                raise EntryError("forbidden")  # a retired or illegal tuple is not rendered
            counterpart_sides = (
                await self._conflict_counterparts(connection, user, call.ctx.read_set, contradictions)
            ).get(identifier, ())
            sources = (
                await sources_for_artifacts(connection, user, [identifier], schema_name=self.runtime.schema_name)
            ).get(identifier, ())
            bounds, summaries = (
                await self._bounds_of(connection, user, [identifier]) if kind == "abstraction" else ({}, {})
            )
            shape: ProcedureShape | None = None
            if kind == "procedure":
                shapes = await shapes_for(
                    connection, user_id=user, procedure_ids=[identifier], schema_name=self.runtime.schema_name
                )
                shape = shapes.get(identifier, NO_SHAPE)

        # Route through the same pure four-channel assembler as ``query`` so every channel invariant holds on ``get``
        # as well: ``I14``/R2-R3 (bounded text + complete bound, refusing ``bound_missing``), R4 (hypothesis label +
        # support summary), R5 (both conflict sides, refusing ``conflict_counterpart_missing``), and R1/R6 (provenance
        # bundle, opaque for unreadable sources). ``get`` names one artifact and has no budget parameter, so the
        # ceiling is sized above this item's rendered token count so it never truncates.
        support_summary = summaries.get(identifier, "")
        ceiling = max(
            2048,
            len(content.split())
            + sum(len(side.text.split()) for side in counterpart_sides)
            + len(support_summary.split())
            + 1024,
        )
        single_budget = ChannelBudgets(
            facts=ceiling,
            hypotheses=ceiling,
            procedures=ceiling,
            conflicts=ceiling,
            total_tokens=None,
            facts_floor_tokens=0,
        )
        assembled = assemble(
            [
                ArtifactView(
                    artifact_id=identifier,
                    kind=cast(Kind, kind),
                    lifecycle=cast(Lifecycle, lifecycle),
                    verification=cast(Verification, verification),
                    text=content,
                    sources=sources,
                    open_contradiction=contradicted,
                    bound=bounds.get(identifier),
                    support_summary=support_summary,
                    counterparts=counterpart_sides,
                )
            ],
            caller_user_id=user,
            read_set=call.ctx.read_set,
            key=self.runtime.provenance_key,
            staleness=lambda _view: stale,
            budgets=single_budget,
        )
        if assembled.refused or assembled.excluded:
            # Refused (missing provenance, missing bound, missing conflict counterpart, illegal tuple) or excluded
            # (retired) is not renderable -- fail closed with ``forbidden`` so absent and unrenderable stay one answer.
            raise EntryError("forbidden")
        if assembled.facts:
            item = assembled.facts[0]
            return Artifact(
                artifact_id=identifier,
                kind=kind,
                lifecycle=lifecycle,
                content=item.text,
                scope_path=scope_path,
                channel="facts",
                verification=verification,
                provenance=item.provenance,
                bound=item.bound if isinstance(item, BoundedFact) else None,
            )
        if assembled.hypotheses:
            hyp = assembled.hypotheses[0]
            return Artifact(
                artifact_id=identifier,
                kind=kind,
                lifecycle=lifecycle,
                content=hyp.text,
                scope_path=scope_path,
                channel="hypotheses",
                verification=verification,
                shape=shape,
                provenance=hyp.provenance,
                label=hyp.label,
                support_summary=hyp.support_summary,
                bound=hyp.bound,
            )
        if assembled.procedures:
            proc = assembled.procedures[0]
            return Artifact(
                artifact_id=identifier,
                kind=kind,
                lifecycle=lifecycle,
                content=proc.text,
                scope_path=scope_path,
                channel="procedures",
                verification=verification,
                shape=shape,
                provenance=proc.provenance,
            )
        if assembled.conflicts:
            conflict = assembled.conflicts[0]
            return Artifact(
                artifact_id=identifier,
                kind=kind,
                lifecycle=lifecycle,
                content=content,
                scope_path=scope_path,
                channel="conflicts",
                verification=verification,
                shape=shape,
                provenance=conflict.provenance,
                conflict_sides=conflict.sides,
            )
        raise EntryError("forbidden")

    async def provenance(self, call: Call) -> ProvenanceView:
        """Return at most eight visible lineage levels; an unreadable source is an opaque leaf."""
        artifact_id = call.arguments.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise EntryError("validation_error", "artifact_id_required")
        depth = call.arguments.get("depth", DEFAULT_PROVENANCE_DEPTH)
        if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= MAX_PROVENANCE_DEPTH:
            raise EntryError("validation_error", "depth_out_of_range")
        user = call.ctx.principal
        try:
            async with self.runtime.session(user) as connection, connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL("SELECT scope_path, visibility FROM {v} WHERE id = %s AND user_id = %s").format(
                        v=self.runtime.table("memory_version")
                    ),
                    (artifact_id, user),
                )
                row = await cursor.fetchone()
                if row is None or not row_visible(call.ctx.read_set, row[0], row[1]):
                    raise EntryError("forbidden")
                view = await provenance_view(
                    connection,
                    user,
                    artifact_id,
                    depth=depth,
                    read_set=call.ctx.read_set,
                    key=self.runtime.provenance_key,
                    schema_name=self.runtime.schema_name,
                )
                # An empty ``edges`` tuple is a truthful answer, not a defect: an artifact whose lineage is only its
                # own content (a staged row, or one admitted before any source was recorded) has no ancestors to
                # show. Report the empty view; ``internal_error`` is never an operation's own logic (``V2-D58``).
                return view
        except InsufficientPrivilege as exc:
            raise EntryError("forbidden") from exc

    # -- temporal modes (E-031, V2-D73) ------------------------------------------------------------------------

    @staticmethod
    def _parse_instant(value: object, field: str) -> datetime:
        """Parse a caller-supplied ISO-8601 instant or bare datetime; require a timezone."""
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                raise EntryError("validation_error", f"{field}_invalid") from None
        else:
            raise EntryError("validation_error", f"{field}_invalid")
        if parsed.tzinfo is None:
            raise EntryError("validation_error", f"{field}_must_carry_a_timezone")
        return parsed.astimezone(UTC)

    def _instant_or_none(self, request: Mapping[str, object], field: str) -> datetime | None:
        raw = request.get(field)
        return None if raw is None else self._parse_instant(raw, field)

    async def _recall_query_for(self, request: Mapping[str, object]) -> RecallQuery | None:
        """The relevance query a request asks for, or ``None`` when it asks only for a temporal read (``V2-D86``).

        ``text`` is the caller's own words. When the deployment configured an
        :class:`~custom_memory_engine.semantic.contracts.Embedder`, the engine computes the query vector itself
        (``V2-D98``), so a caller sends a question and receives full hybrid recall without owning a model. A caller
        that supplies ``embedding``/``embedding_model`` still wins: that is the advanced path, and the vector it sent
        is the one that ranks. A malformed supplied vector is refused instead of dropped -- a caller who sent one
        believes it was used.
        """
        text = request.get("text")
        if text is None:
            if request.get("embedding") is not None or request.get("embedding_model") is not None:
                raise EntryError("validation_error", "text_required_for_embedding")
            return None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_QUERY_TEXT:
            raise EntryError("validation_error", "text_invalid")
        if "\x00" in text:
            # PostgreSQL text cannot hold a NUL byte, so a query containing one would surface as an internal error from
            # the driver rather than as the validation refusal it is. Refused here, where the code is static.
            raise EntryError("validation_error", "text_invalid")
        raw_embedding = request.get("embedding")
        embedding: tuple[float, ...] | None = None
        if raw_embedding is not None:
            if not isinstance(raw_embedding, Sequence) or isinstance(raw_embedding, str | bytes):
                raise EntryError("validation_error", "embedding_invalid")
            embedding = tuple(raw_embedding)  # type: ignore[arg-type]
        model = request.get("embedding_model")
        if embedding is None and self.embedder is not None:
            embedded = await self._query_vector(text)
            if embedded is not None:
                embedding, embedding_model = embedded
                try:
                    return RecallQuery(text=text, embedding=embedding, embedding_model=embedding_model)
                except RecallError as exc:
                    raise EntryError("validation_error", exc.code) from None
        try:
            return RecallQuery(
                text=text,
                embedding=embedding,
                embedding_model=model if isinstance(model, str) else None,
            )
        except RecallError as exc:
            raise EntryError("validation_error", exc.code) from None

    async def _query_vector(self, text: str) -> tuple[tuple[float, ...], str] | None:
        """The dense arm's query vector, from the deployment's embedder, or ``None`` to rank lexically.

        **Failure degrades, and says so.** A model that cannot load (a missing file, a corrupt graph, an exhausted
        thread pool) must not turn every read into ``internal_error``: the lexical arms are still correct, they are
        just less capable. What must not happen is a *silent* degradation, so the failure is logged with a static code
        and the result reports the arms that actually ran (:attr:`QueryResult.arms`), which is what a caller or a
        benchmark reads to know whether it received a hybrid answer.

        A width the schema cannot index is refused here for the same reason: it is a deployment fault (a model file
        swapped under the pinned asset), and the honest response is to rank lexically and log the mismatch rather than
        to answer every caller with ``validation_error`` about a vector they never sent.
        """
        assert self.embedder is not None
        try:
            vectors = await self.embedder.embed([text], query=True)
        except Exception as error:  # noqa: BLE001 - any model failure degrades the arm, never the read
            logger.warning(
                "query_embedding_unavailable",
                code=getattr(error, "code", "embedding_failed"),
                error_type=type(error).__name__,
            )
            return None
        vector = tuple(float(value) for value in vectors[0]) if vectors else ()
        try:
            slot_for_dimension(len(vector))
        except RecallError as error:
            logger.warning("query_embedding_unavailable", code=error.code, error_type="RecallError")
            return None
        return vector, self.embedder.model

    def _intent_for(self, mode: str, request: Mapping[str, object], limit: int, scope: str | None) -> Intent:
        """Derive the planner's :class:`Intent` from the request's own fields (``E-031`` §5.1: no second vocabulary).

        The four instants are parsed first and the mode's required ones checked after, in the order and with the codes
        ``query`` has always used, so no caller sees a different error. Only shape is checked here; the relations
        between instants and ρ (a target after the reference, an empty window) need the engine clock and are checked
        at execution, inside the read transaction, exactly as before.
        """
        target = self._instant_or_none(request, "target_time")
        belief = self._instant_or_none(request, "belief_time")
        start = self._instant_or_none(request, "window_start")
        end = self._instant_or_none(request, "window_end")
        if mode in ("historical_as_of", "audit_as_of") and target is None:
            raise EntryError("validation_error", "target_time_required")
        if mode in ("historical_window", CHANGE_MODE):
            # `change` (V2-D81) compares the same two instants the window form bounds, under the same names and the
            # same codes, so a caller learns one vocabulary rather than two.
            if start is None:
                raise EntryError("validation_error", "window_start_required")
            if end is None:
                raise EntryError("validation_error", "window_end_required")
        if mode == "audit_as_of" and belief is None:
            raise EntryError("validation_error", "belief_time_required")
        try:
            return Intent(
                mode=cast(PlanMode, mode),
                limit=limit,
                target_time=target,
                belief_time=belief,
                window_start=start,
                window_end=end,
                scope=scope,
                text=_text_or_none(request.get("text")),
            )
        except PlanningError as exc:
            raise EntryError("validation_error", exc.code) from None

    async def _recall(
        self,
        connection: AsyncConnection,
        plan: Plan,
        temporal: TemporalQuery,
        read_filter: sql.Composable,
        read_parameters: Mapping[str, object],
        recall_query: RecallQuery | None,
    ) -> Recall | None:
        """The plan's hybrid recall, or ``None`` when the plan is the recency scan.

        The plan carries a **digest** of the question, never the question. A hybrid plan replayed with a different
        text is refused rather than silently ranked by it, which is what makes a recorded plan name the question it
        answered (``V2-D86``) without putting caller content in a value a recorder may persist.
        """
        if plan.recall != HYBRID_RECALL:
            return None
        if recall_query is None:
            raise EntryError("validation_error", "plan_query_missing")
        try:
            if plan.query_digest != query_digest(recall_query.text):
                raise EntryError("validation_error", "plan_query_mismatch")
            return await hybrid_recall(
                connection,
                temporal,
                read_filter,
                read_parameters,
                recall_query,
                limit=plan.rows,
                schema_name=self.runtime.schema_name,
            )
        except RecallError as exc:
            raise EntryError("validation_error", exc.code) from None

    async def _judge(self, query: str, rows: Sequence[ScopedRow]) -> tuple[tuple[ScopedRow, ...], Judgement | None]:
        """Read the page with the relevance judge and keep only what it calls relevant (``V2-D104``).

        **Four decisions, each with its reason.**

        *Only the first* :data:`~engine.hybrid.JUDGE_CANDIDATES` *rows are read, and the rest are dropped.* The judge
        costs ~28 ms per pair, so an unbounded page is an unbounded read-path cost; and a gate that silently stopped
        applying to the tail of the page would report "nothing relevant" while rows it never read were about to be
        returned. Truncating is the only bound that is honest about what it decided.

        *A failing judge degrades to the fused order and reports* ``judgement=None``. The arms are still correct, just
        less decisive — and a model failure must not turn every read into ``internal_error`` (the same rule the query
        embedder follows, ``V2-D98``). ``judgement=None`` is what makes the degradation visible rather than silent.

        *A score-count mismatch is a programming error, not a verdict.* It degrades the same way: refusing every read
        because a scorer returned four numbers for five candidates would be worse than ranking them.

        *Order is the judge's, ties keep the fused order.* A judge that ranks two candidates equally has expressed no
        preference between them, and the fused order already used three arms to break that tie.
        """
        assert self.judge is not None
        candidates = tuple(rows[:JUDGE_CANDIDATES])
        if not candidates:
            return (), Judgement(model=self.judge.model, floor=self.judge_floor, scored=0, kept=0, best=None)
        try:
            raw = await self.judge.relevance(query, [row.content for row in candidates])
        except Exception as error:  # noqa: BLE001 - any judge failure degrades the stage, never the read
            logger.warning(
                "relevance_judge_unavailable",
                code=getattr(error, "code", "judge_failed"),
                error_type=type(error).__name__,
            )
            return tuple(rows), None
        scores = tuple(float(value) for value in raw)
        if len(scores) != len(candidates):
            logger.warning("relevance_judge_score_count_mismatch", expected=len(candidates), received=len(scores))
            return tuple(rows), None
        if not all(isfinite(value) for value in scores):
            logger.warning("relevance_judge_score_not_finite", scored=len(scores))
            return tuple(rows), None
        order = sorted(range(len(candidates)), key=lambda index: (-scores[index], index))
        kept = tuple(candidates[index] for index in order if scores[index] >= self.judge_floor)
        return kept, Judgement(
            model=self.judge.model, floor=self.judge_floor, scored=len(scores), kept=len(kept), best=max(scores)
        )

    @staticmethod
    def _temporal_query_for(plan: Plan, user: str, reference_time: datetime) -> TemporalQuery:
        """The plan's temporal filter as a :class:`TemporalQuery`, with ρ read by the executor (``V2-D73``).

        ρ is never in a plan: it is the clock writes record against, read inside this transaction, so a replay asks the
        recorded question of the engine's *present* rather than of a caller-chosen instant.
        """
        try:
            if plan.mode == "current_state":
                return current_state(user, reference_time)
            if plan.mode == "historical_as_of":
                target = plan.instant("target_time")
                assert target is not None  # the plan's own invariant: historical_as_of carries target_time
                return historical_as_of(user, target, reference_time)
            if plan.mode == "historical_window":
                start, end = plan.instant("window_start"), plan.instant("window_end")
                assert start is not None and end is not None
                return historical_window(user, start, end, reference_time)
            target, belief = plan.instant("target_time"), plan.instant("belief_time")
            assert target is not None and belief is not None
            return audit_as_of(user, target, belief)
        except TemporalQueryError as exc:
            raise EntryError("validation_error", exc.code) from None
        except ValueError:
            # a TemporalQuery field validator rejected the combination; the code is a static literal
            raise EntryError("validation_error", "temporal_parameter_mismatch") from None

    @staticmethod
    def _change_queries_for(plan: Plan, user: str, reference_time: datetime) -> tuple[TemporalQuery, TemporalQuery]:
        """``V2-D81``: the two ``historical_as_of`` views a ``change`` plan diffs, both under ρ.

        The pair is validated as the window it spans first, so ``change`` refuses an empty or future span with the
        same codes ``historical_window`` uses (``historical_window_must_be_nonempty``,
        ``historical_window_after_reference``) and a caller learns one vocabulary rather than two.
        """
        start, end = plan.instant("window_start"), plan.instant("window_end")
        assert start is not None and end is not None  # the plan's own invariant: change carries both instants
        try:
            historical_window(user, start, end, reference_time)
            return historical_as_of(user, start, reference_time), historical_as_of(user, end, reference_time)
        except TemporalQueryError as exc:
            raise EntryError("validation_error", exc.code) from None
        except ValueError:
            raise EntryError("validation_error", "temporal_parameter_mismatch") from None

    async def _applicable_rows(
        self,
        connection: AsyncConnection,
        user: str,
        plan: Plan,
        rows: tuple[ScopedRow, ...],
        shapes: Mapping[str, ProcedureShape],
    ) -> tuple[ScopedRow, ...]:
        """``rows`` minus every procedure whose preconditions do not hold for this request over these rows.

        The artifact set the conditions see is every row the request retrieved (after the read re-check), procedures
        included, in retrieval order. The facet/predicate read is skipped when no procedure carries a condition, so a
        query with no shaped procedure costs exactly what it did before ``E-030``.
        """
        procedure_ids = [row.id for row in rows if row.kind == "procedure"]
        if not any(shapes.get(procedure_id, NO_SHAPE).preconditions for procedure_id in procedure_ids):
            return rows
        try:
            request = ApplicabilityRequest(scope=plan.scope)
        except ScopeError:
            raise EntryError("validation_error", "scope_invalid") from None
        artifacts = await self._facts_of(connection, user, rows)
        kept = set(applicable(procedure_ids, shapes, request, artifacts))
        return tuple(row for row in rows if row.kind != "procedure" or row.id in kept)

    @staticmethod
    def _within(plan: Plan, read_set: ReadSet) -> ReadSet:
        """The plan's read scopes as a :class:`ReadSet`, **refusing** any scope the caller does not hold (``I7``).

        The planner builds its scopes from the caller's own read set, so in the ``query`` path this never refuses. It
        exists for replay: a recorded plan is data, and data must not be able to widen what a caller reads. A scope is
        held when one of the caller's entries covers its path *and* every visibility it names, under the same rule
        ``_granted_for`` re-anchors by.
        """
        entries: list[ReadEntry] = []
        for scope in plan.scopes:
            wanted = frozenset(scope.visibilities)
            if not wanted <= _ALL_VISIBILITIES:
                raise EntryError("forbidden")
            entries.append(ReadEntry(scope.path, cast(frozenset[Visibility], wanted), cast(SelectorMode, scope.mode)))
        effective = ReadSet(tuple(entries), ())
        if not covers(read_set, effective):
            raise EntryError("forbidden")
        return effective

    async def execute_plan(
        self,
        call: Call,
        plan: Plan,
        *,
        budgets: ChannelBudgets | None = None,
        narrowing: Narrowing | None = None,
        recall_query: RecallQuery | None = None,
        _collector: StageCollector | None = None,
        _operation: CostOperation = "query",
    ) -> QueryResult:
        """Run ``plan`` for ``call``: the execution half of ``query``, separate from planning (``E-031`` W2).

        Stages, in plan order, each inside the observer's boundary (``E-036`` measures these):

        * ``candidate_generation`` -- in: the plan's filters and row limit; out: ``ScopedRow``s. The temporal
          predicate, the typed-artifact guard and the read predicate run in **one** statement before ``LIMIT``, so the
          limit counts rows the caller may read (``V2-D73``). When the plan's ``recall`` is ``hybrid`` (``V2-D86``) the
          candidate set is first ranked by relevance to ``recall_query`` and the same statement then fetches exactly
          those ids, so relevance decides the **order** of authorized rows and never their membership; a relevance
          query that matches nothing answers nothing rather than falling back to recency.
        * ``expansion`` -- in: candidate rows; out: those rows followed by the reached rows in minimum-hop order.
          The temporal and read predicates run before each reached row enters the next hop (``V2-D76``).
        * ``filter_rerank`` -- in: rows; out: rows that pass the ``V2-D45`` re-check and, on an answer route, the
          ``E-030`` applicability filter (a non-applicable procedure is removed), with the per-row facts routing needs
          (verification, open contradictions, outcome counters, procedure shapes). No score is computed here.
        * ``assemble`` -- in: rows and facts; out: :class:`QueryResult`. Channel routing by ``channel_of``; then, and
          only then, the ``V2-D75`` procedure ordering, so no statistic can move an item between channels.

        The temporal filter runs at the first stage that sees each row. ``assemble`` sees a row's
        ``(kind, lifecycle, verification, contradiction)`` and never the mode, which is what makes the temporal
        predicate a filter rather than a score term (``ARCHITECTURE_V2.md`` §9.3, criterion 5).

        ``audit_as_of`` takes the same path but its plan's route is ``audit``: its rows come back in ``audit`` with
        ``is_audit=True`` and never touch a channel bucket, because ``channel_of`` maps a retired artifact to ``none``
        and would drop exactly the rows an audit exists to show.
        """
        user = call.ctx.principal
        # Which ranking arms ran, reported on the result (see `QueryResult.arms`); empty unless the plan ranked.
        ranked_arms: tuple[str, ...] = ()
        judgement: Judgement | None = None
        is_audit = plan.route == "audit"
        if is_audit and call.ctx.tier != "C":
            raise EntryError("forbidden")  # API_V2.md §4.2: audit_as_of is Tier C only
        effective_read_set = self._within(plan, call.ctx.read_set)
        if effective_read_set.empty:
            return QueryResult(is_audit=is_audit)
        if budgets is None:
            request = call.arguments.get("request")
            try:
                budgets = ChannelBudgets.from_value(request.get("budget") if isinstance(request, Mapping) else None)
            except ChannelError as exc:
                raise EntryError("validation_error", exc.code) from None
        read_filter, read_parameters = read_predicate_named(effective_read_set, "m.scope_path", "m.visibility")
        if narrowing is not None:
            # E-034: a drill-down fetches the prior result's own ids, never a fresh page of the read set. The id
            # restriction is composed into the same statement *before* LIMIT, beside the temporal and read predicates,
            # so a candidate the caller may no longer read (or that is no longer believed) is simply absent.
            read_filter = sql.SQL("({}) AND m.id = ANY(%(candidate_ids)s)").format(read_filter)
            read_parameters = {**read_parameters, "candidate_ids": list(narrowing.candidates)}
        source_map: Mapping[str, tuple[ProvenanceSource, ...]] = {}
        bounds: dict[str, Bound] = {}
        support_summaries: dict[str, str] = {}
        collector = _collector
        if collector is None and self._collect_cost:
            collector = self._new_collector()
            assert collector is not None
            collector.record_bypassed("plan", candidates_in=len(plan.scopes), candidates_out=len(plan.scopes))
        try:
            async with self.runtime.session(user) as connection:
                with self._stage(collector, "candidate_generation") as span:
                    if span is not None:
                        span.candidates_in = len(narrowing.candidates) if narrowing is not None else plan.rows
                    # the reference instant is the clock writes record against, read inside this transaction (I4)
                    reference_time = await reference_instant(connection)
                    previous_view: tuple[ScopedRow, ...] = ()
                    current_view: tuple[ScopedRow, ...] = ()
                    if plan.mode == CHANGE_MODE:
                        previous_query, tquery = self._change_queries_for(plan, user, reference_time)
                        # One row past the limit, so a truncated view is *detected*: a diff of a truncated view
                        # would report as removed (or added) a row that was merely not fetched (V2-D81).
                        previous_view = await fetch_scoped_rows(
                            connection,
                            previous_query,
                            read_filter,
                            read_parameters,
                            limit=plan.rows + 1,
                            schema_name=self.runtime.schema_name,
                            with_identity=True,
                        )
                        current_view = await fetch_scoped_rows(
                            connection,
                            tquery,
                            read_filter,
                            read_parameters,
                            limit=plan.rows + 1,
                            schema_name=self.runtime.schema_name,
                            with_identity=True,
                        )
                        if len(previous_view) > plan.rows or len(current_view) > plan.rows:
                            raise EntryError("resource_limit", "change_view_exceeds_limit")
                        current_ids = {row.id for row in current_view}
                        rows = current_view + tuple(row for row in previous_view if row.id not in current_ids)
                    else:
                        tquery = self._temporal_query_for(plan, user, reference_time)
                        recall = await self._recall(
                            connection, plan, tquery, read_filter, read_parameters, recall_query
                        )
                        if recall is None:
                            rows = await fetch_scoped_rows(
                                connection,
                                tquery,
                                read_filter,
                                read_parameters,
                                limit=plan.rows,
                                schema_name=self.runtime.schema_name,
                            )
                        elif not recall.ordered:
                            # A relevance query with no match is an answer, not an error and not a recency dump.
                            rows = ()
                            if self.judge is not None:
                                # Nothing to judge is a verdict, not a missing one: the arms found no candidate, so
                                # the page is empty *and* the result says the gate ran and passed nothing.
                                judgement = Judgement(
                                    model=self.judge.model, floor=self.judge_floor, scored=0, kept=0, best=None
                                )
                        else:
                            # The ids are composed into the *filter*, beside the temporal and read predicates and
                            # before LIMIT, so the fetcher cannot return a row the recall did not authorize even if
                            # the two statements were to disagree about what is current.
                            rank_filter = sql.SQL("({}) AND m.id = ANY(%(recall_ids)s)").format(read_filter)
                            fetched = await fetch_scoped_rows(
                                connection,
                                tquery,
                                rank_filter,
                                {**read_parameters, "recall_ids": list(recall.ordered)},
                                limit=len(recall.ordered),
                                schema_name=self.runtime.schema_name,
                            )
                            position = {version_id: index for index, version_id in enumerate(recall.ordered)}
                            rows = tuple(sorted(fetched, key=lambda row: position.get(row.id, len(position))))
                            # `V2-D104`: a relevance query is answered only if something in the page is actually
                            # relevant. Applied here, where the candidate rows and their content are already in hand,
                            # so the judge costs one forward pass per candidate and never a second fetch — and so this
                            # module stays the only place that knows the ranking arms exist.
                            if self.judge is not None and recall_query is not None:
                                rows, judgement = await self._judge(recall_query.text, rows)
                        if recall is not None:
                            ranked_arms = tuple(arm.arm for arm in recall.arms)
                    if span is not None:
                        span.candidates_out = len(rows)
                with self._stage(collector, "expansion") as span:
                    if span is not None:
                        span.candidates_in = len(rows)
                    if plan.expansion == ASSOCIATION_EXPANSION:
                        rows += await expand_associations(
                            connection,
                            tquery,
                            effective_read_set,
                            [row.id for row in rows],
                            database_clock=reference_time,
                            # the plan's own budget (E-032-F3): a replay traverses the depth it recorded
                            hop_budget=plan.hop_budget,
                            # the response bound (E-032-F2): at most one page of reached rows, so a dense graph cannot
                            # make one request return 32 x limit rows
                            row_budget=plan.rows,
                            schema_name=self.runtime.schema_name,
                        )
                    if span is not None:
                        span.candidates_out = len(rows)
                with self._stage(collector, "filter_rerank") as span:
                    if span is not None:
                        span.candidates_in = len(rows)
                    # Defence in depth (V2-D45): the reference semantics must agree with the SQL predicate. Run first
                    # so an invisible row can never feed metadata lookups, procedure preconditions or narrowing.
                    rows = tuple(
                        row for row in rows if row_visible(effective_read_set, row.scope_path, row.visibility)
                    )
                    ids = [row.id for row in rows]
                    verifications = await self._verification_of(connection, user, ids)
                    contradictions = await self._contradictions(connection, user, ids)
                    contradicted = set(contradictions)
                    # E-038-F5: derived staleness on every answer route; never on an audit (E-038-F3)
                    stale = set() if is_audit else await self._stale_ids(connection, user, verifications)
                    # E-030: each procedure's preconditions and steps, evaluated against the live candidate set
                    # **before** any refine narrowing so drilling into `channel procedures` or `artifact <proc>` does
                    # not discard sibling artifacts required by an `exists{...}` precondition (while a sibling that
                    # was actually deleted/purged is absent from `rows` and honestly drops the procedure).
                    procedure_ids = [row.id for row in rows if row.kind == "procedure"]
                    shapes = await shapes_for(
                        connection, user_id=user, procedure_ids=procedure_ids, schema_name=self.runtime.schema_name
                    )
                    if PROCEDURE_APPLICABILITY in plan.filters:
                        rows = await self._applicable_rows(connection, user, plan, rows, shapes)
                    if narrowing is not None:
                        # E-034: the instruction narrows *before* assembly, so coverage and cost report the drilled
                        # candidate set honestly, and a kept row reaches `channel_of` unchanged (membership is only
                        # ever narrowed, never moved). A `channel` instruction is evaluated with `channel_of` itself.
                        rows = tuple(
                            row
                            for row in rows
                            if narrowing.instruction.keeps(
                                artifact_id=row.id,
                                scope_path=row.scope_path,
                                content=row.content,
                                channel=_channel_name(row, verifications, contradicted, stale),
                            )
                        )
                    # E-027: outcome counters for the surviving procedure rows, fetched in one statement.
                    surviving_procedure_ids = [row.id for row in rows if row.kind == "procedure"]
                    stats = await outcome_stats_for(
                        connection,
                        user_id=user,
                        procedure_ids=surviving_procedure_ids,
                        schema_name=self.runtime.schema_name,
                    )
                    counterpart_sides: dict[str, tuple[ConflictSide, ...]] = {}
                    if not is_audit:
                        # V2-D80: each artifact's own counterparts, loaded whether or not they were retrieved
                        surviving = {row.id for row in rows}
                        counterpart_sides = await self._conflict_counterparts(
                            connection,
                            user,
                            effective_read_set,
                            {key: value for key, value in contradictions.items() if key in surviving},
                        )
                        source_map = await sources_for_artifacts(
                            connection, user, [row.id for row in rows], schema_name=self.runtime.schema_name
                        )
                        bounds, support_summaries = await self._bounds_of(
                            connection, user, [row.id for row in rows if row.kind == "abstraction"]
                        )
                    if span is not None:
                        span.candidates_out = len(rows)
        except InsufficientPrivilege as exc:
            raise EntryError("forbidden") from exc
        except TemporalQueryError as exc:
            raise EntryError("validation_error", exc.code) from exc

        if is_audit:
            with self._stage(collector, "assemble") as span:
                if span is not None:
                    span.candidates_in = len(rows)
                audited: list[Artifact] = []
                refused: list[tuple[str, str]] = []
                for row in rows:
                    verification = _verification_for(row.lifecycle, verifications.get(row.id))
                    channel = _channel_for(
                        row.kind, row.lifecycle, verification, row.id in contradicted, row.id in stale
                    )
                    if channel is None:
                        refused.append((row.id, "illegal_artifact_tuple"))
                        continue
                    audited.append(
                        Artifact(
                            artifact_id=row.id,
                            kind=row.kind,
                            lifecycle=row.lifecycle,
                            content=row.content,
                            scope_path=row.scope_path,
                            channel=channel,
                            verification=verification,
                            shape=shapes.get(row.id, NO_SHAPE) if row.kind == "procedure" else None,
                            hop_distance=row.hop_distance,
                        )
                    )
                if span is not None:
                    span.candidates_out = len(audited)
            stage_costs = self._finalize_cost(collector, operation=_operation, plan=plan)
            return QueryResult(audit=audited, refused=refused, is_audit=True, stage_costs=stage_costs)

        with self._stage(collector, "assemble") as span:
            rows_by_id = {row.id: row for row in rows}
            views: list[ArtifactView] = []
            for row in rows:
                counterparts = counterpart_sides.get(row.id, ())
                views.append(
                    ArtifactView(
                        artifact_id=row.id,
                        kind=cast(Kind, row.kind),
                        lifecycle=cast(Lifecycle, row.lifecycle),
                        verification=cast(Verification, _verification_for(row.lifecycle, verifications.get(row.id))),
                        text=row.content,
                        sources=source_map.get(row.id, ()),
                        open_contradiction=row.id in contradicted,
                        bound=bounds.get(row.id),
                        support_summary=support_summaries.get(row.id, ""),
                        counterparts=counterparts,
                    )
                )
            assembled = assemble(
                views,
                caller_user_id=user,
                read_set=effective_read_set,
                key=self.runtime.provenance_key,
                staleness=lambda view: view.artifact_id in stale,
                budgets=budgets,
            )
            if span is not None:
                span.candidates_in = assembled.cost.candidates_considered
                span.candidates_out = assembled.cost.items_rendered
                span.tokens_considered = assembled.cost.tokens_considered
                span.tokens_rendered = assembled.cost.tokens_rendered

            def to_artifact(
                artifact_id: str,
                channel: Channel,
                content: str,
                provenance: tuple[ProvenanceReference, ...],
                *,
                label: str | None = None,
                support_summary: str = "",
                bound: Bound | None = None,
                conflict_sides: tuple[ConflictSide, ...] = (),
            ) -> Artifact:
                row = rows_by_id[artifact_id]
                return Artifact(
                    artifact_id=artifact_id,
                    kind=row.kind,
                    lifecycle=row.lifecycle,
                    content=content,
                    scope_path=row.scope_path,
                    channel=channel,
                    verification=_verification_for(row.lifecycle, verifications.get(artifact_id)),
                    shape=shapes.get(artifact_id, NO_SHAPE) if row.kind == "procedure" else None,
                    hop_distance=row.hop_distance,
                    provenance=provenance,
                    label=label,
                    support_summary=support_summary,
                    bound=bound,
                    conflict_sides=conflict_sides,
                )

            facts = []
            for item in assembled.facts:
                bound = item.bound if isinstance(item, BoundedFact) else None
                facts.append(to_artifact(item.artifact_id, "facts", item.text, item.provenance, bound=bound))
            hypotheses = [
                to_artifact(
                    item.artifact_id,
                    "hypotheses",
                    item.text,
                    item.provenance,
                    label=item.label,
                    support_summary=item.support_summary,
                    bound=item.bound,
                )
                for item in assembled.hypotheses
            ]
            procedure_items = {
                item.artifact_id: to_artifact(item.artifact_id, "procedures", item.text, item.provenance)
                for item in assembled.procedures
            }
            if procedure_items:
                procedure_order = order_procedures(procedure_items, stats)
                procedures = [procedure_items[artifact_id] for artifact_id in procedure_order]
            else:
                procedures = []
            conflicts = [
                to_artifact(
                    item.artifact_id,
                    "conflicts",
                    rows_by_id[item.artifact_id].content,
                    item.provenance,
                    conflict_sides=item.sides,
                )
                for item in assembled.conflicts
            ]
            changes: tuple[Change, ...] = ()
            if plan.mode == CHANGE_MODE:
                # `query` keeps the unfiltered diff it always reported (W6). A *refine* keeps only the changes that
                # touch a row it kept, each **whole and with its original outcome**: diffing the narrowed views
                # instead would re-key the report, so narrowing to one side of a `changed` key would misreport it as
                # `added` or `removed`, a change the drilled-into result never contained.
                changes = diff_views(previous_view, current_view)
                if narrowing is not None:
                    kept = {row.id for row in rows}
                    changes = tuple(
                        change for change in changes if kept.intersection((*change.previous, *change.current))
                    )
        stage_costs = self._finalize_cost(collector, operation=_operation, plan=plan)
        return QueryResult(
            facts=facts,
            hypotheses=hypotheses,
            procedures=procedures,
            conflicts=conflicts,
            refused=[(item.artifact_id, item.reason) for item in assembled.refused],
            coverage=assembled.coverage,
            cost=assembled.cost,
            changes=changes,
            stage_costs=stage_costs,
            arms=ranked_arms,
            judgement=judgement,
        )

    async def query(self, call: Call) -> QueryResult:
        request = call.arguments.get("request")
        if not isinstance(request, Mapping):
            raise EntryError("validation_error", "request_required")
        try:
            budgets = ChannelBudgets.from_value(request.get("budget"))
        except ChannelError as exc:
            raise EntryError("validation_error", exc.code) from None
        limit = request.get("limit", DEFAULT_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise EntryError("validation_error", "limit_out_of_range")
        mode = request.get("mode", "current_state")
        if mode not in TEMPORAL_MODES and mode != CHANGE_MODE:
            raise EntryError("validation_error", "mode_invalid")
        if "reference_time" in request:
            raise EntryError("validation_error", "reference_time_is_the_engine_clock")  # I4
        if request.get("text") is not None and mode in (CHANGE_MODE, "audit_as_of"):
            # `V2-D86`: neither mode ranks. A diff must report every authorized row and an audit must report a past
            # belief unchanged, so a question supplied to either is refused rather than quietly ignored -- a caller who
            # sent one believes it was used.
            raise EntryError("validation_error", "text_unsupported_for_mode")
        scope = request.get("scope")
        requested_path: str | None = None
        if scope is not None:
            try:
                requested_path = parse_scope(scope).text
            except ScopeError:
                raise EntryError("validation_error", "scope_invalid") from None
            # The requested scope must lie inside the caller's resolved read set; it is re-anchored there and
            # never widened (visibilities and mode come from the grant, so V2 §5 rule 4 still holds).
            effective_read_set = ReadSet(self._granted_for(call.ctx.read_set, requested_path), ())
            if effective_read_set.empty:
                raise EntryError("forbidden")
        else:
            effective_read_set = call.ctx.read_set
        if mode == "audit_as_of" and call.ctx.tier != "C":
            raise EntryError("forbidden")  # checked before the empty-read-set shortcut, so it never answers
        recall_query = await self._recall_query_for(request)
        intent = self._intent_for(str(mode), request, limit, requested_path)
        if effective_read_set.empty:
            # Nothing can be read, so nothing runs; the answer is still an answer and still gets a handle, so a
            # caller can drill into it exactly like any other result (``_issue``: every answer path issues one).
            return self._issue(
                call,
                read_set=effective_read_set,
                intent=intent,
                budgets=budgets,
                result=QueryResult(is_audit=mode == "audit_as_of"),
                depth=0,
            )
        if len(effective_read_set.entries) > MAX_SELECTORS:
            raise EntryError("resource_limit", "too_many_selectors")
        collector = self._new_collector()
        with self._stage(collector, "plan") as span:
            if span is not None:
                span.candidates_in = len(effective_read_set.entries)
            try:
                plan = plan_retrieval(intent, effective_read_set, planner=self.planner)
            except PlanningError as exc:
                raise EntryError("validation_error", exc.code) from None
            if span is not None:
                span.candidates_out = len(plan.scopes)
        self.recorder(plan)
        result = await self.execute_plan(
            call,
            plan,
            budgets=budgets,
            recall_query=recall_query,
            _collector=collector,
            _operation="query",
        )
        if result.stage_costs is not None:
            self.costs.record(call.ctx.principal, result.stage_costs)
        return self._issue(call, read_set=effective_read_set, intent=intent, budgets=budgets, result=result, depth=0)

    async def refine(self, call: Call) -> QueryResult:
        """Drill into a prior result, within its read set (``API_V2.md`` §4.2; ``E-034``, ``V2-D83``).

        The entry point has already resolved ``result_id`` to the snapshot the ``query`` left and answered
        ``forbidden`` for an unknown, foreign or expired handle, and for a credential that no longer holds the
        recorded read set. Everything here therefore *narrows*: the plan is the recorded question with expansion
        cut off (reaching a neighbour would be a widening), the statement fetches only the result's own candidate
        ids, and the instruction is applied to those rows before assembly, so channel membership and bounds are
        exactly what the original result had. Nothing can add a row.
        """
        snapshot = call.result
        if snapshot is None:
            # Unreachable through `Engine.call` (the condition resolves the handle first); a direct handler call has
            # no authorised result to drill into and must fail closed rather than fall back to a fresh query.
            raise EntryError("forbidden")
        if snapshot.intent.mode == "audit_as_of" and call.ctx.tier != "C":
            # API_V2.md §4.2: audit_as_of is Tier C only, including when refining an empty or non-empty audit handle.
            raise EntryError("forbidden")
        # Depth first: an exhausted chain cannot drill again whatever it asked for, so the answer does not depend on
        # whether the instruction also happens to be malformed (one precedence, not two).
        if snapshot.depth >= MAX_REFINE_DEPTH:
            raise EntryError("validation_error", "refine_depth_exceeded")
        try:
            instruction = parse_instruction(call.arguments.get("instruction"))
        except InstructionError as exc:
            raise EntryError("validation_error", exc.code) from None
        if not snapshot.candidates:
            # The prior question answered nothing, so there is nothing to narrow. No statement runs, and the chain
            # still returns a result of the same shape with a fresh handle instead of an error for asking exactly.
            return self._issue(
                call,
                read_set=snapshot.read_set,
                intent=snapshot.intent,
                budgets=snapshot.budgets,
                result=QueryResult(is_audit=snapshot.intent.mode == "audit_as_of"),
                depth=snapshot.depth + 1,
            )
        collector = self._new_collector()
        with self._stage(collector, "plan") as span:
            if span is not None:
                span.candidates_in = len(snapshot.read_set.entries)
            try:
                plan = refine_plan(
                    plan_retrieval(snapshot.intent, snapshot.read_set, planner=self.planner),
                    candidates=len(snapshot.candidates),
                )
            except PlanningError as exc:
                raise EntryError("validation_error", exc.code) from None
            if span is not None:
                span.candidates_out = len(plan.scopes)
        self.recorder(plan)
        # Execute under the *recorded* read set, never the caller's current one. The entry point proved the current
        # credential still covers it, so this is a narrowing by construction: a widened credential changes nothing,
        # and provenance renders against the same set, keeping a foreign source opaque.
        recorded = replace(call, ctx=replace(call.ctx, read_set=snapshot.read_set))
        # The original answer's budgets, not this call's: a refine has no ``request`` to read them from, and
        # defaulting would let a drill-down return rows the result it names never contained (or fewer of them).
        result = await self.execute_plan(
            recorded,
            plan,
            budgets=snapshot.budgets,
            narrowing=Narrowing(snapshot.candidates, instruction),
            _collector=collector,
            _operation="refine",
        )
        if result.stage_costs is not None:
            self.costs.record(call.ctx.principal, result.stage_costs)
        return self._issue(
            call,
            read_set=snapshot.read_set,
            intent=snapshot.intent,
            budgets=snapshot.budgets,
            result=result,
            depth=snapshot.depth + 1,
        )

    async def stats(self, call: Call) -> ArtifactStats:
        """One artifact's counts, coverage, verification and lineage ids — never its content (§4.11).

        ``targets_readable`` has already proved the caller may read ``artifact_id``; this reads under the caller's own
        row-level security as well, so every id the ledger names is one the caller could have fetched through ``get``.
        The staleness causes are derived here exactly as the read path derives them (``V2-D24``): a stored flag would
        be the second source of truth this operation exists to avoid.
        """
        artifact_id = call.arguments.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise EntryError("validation_error", "artifact_id_required")
        user = call.ctx.principal
        async with self.runtime.session(user) as connection:
            try:
                return await read_stats(
                    connection,
                    user_id=user,
                    artifact_id=artifact_id,
                    cache=self.closure_cache,
                    schema_name=self.runtime.schema_name,
                )
            except StatsError as exc:
                # `targets_readable` resolved this id for an authorised caller, so a missing row here is the same
                # "absent and unreadable are one answer" case the rest of the read path answers with `forbidden`.
                raise EntryError("forbidden") from exc

    async def cost(self, call: Call) -> CostReport:
        """Return per-stage latency, candidate counts and tokens for the caller's own queries (``API_V2.md`` §4.11)."""
        try:
            return self.costs.report(call.ctx.principal, window=call.arguments.get("window"))
        except CostError as exc:
            raise EntryError("validation_error", exc.code) from None

    def _issue(
        self,
        call: Call,
        *,
        read_set: ReadSet,
        intent: Intent,
        budgets: ChannelBudgets,
        result: QueryResult,
        depth: int,
    ) -> QueryResult:
        """Attach a fresh drill-down handle to ``result`` and return it (``E-034``).

        Every answer path issues one, so a caller can always drill again; the handle records the read set, the intent
        and the channel budgets of *this* call, which is what confines and shapes the next refine.
        """
        result.result_id = self.results.issue(
            principal=call.ctx.principal,
            read_set=read_set,
            intent=intent,
            budgets=budgets,
            candidates=_result_ids(result),
            depth=depth,
        )
        return result

    @staticmethod
    def _granted_for(read_set: ReadSet, path: str) -> tuple[ReadEntry, ...]:
        """The caller's own grants that cover ``path``, re-anchored at ``path``.

        A caller reading ``/u/alice/p/repo/`` holds that subtree through its ``/u/alice/`` grant. Re-anchoring keeps
        the granted visibilities (so nothing widens) and switches to ``exact`` only where the grant was ``exact`` —
        a subtree grant stays a subtree, because the request is genuinely inside it. An ``exact`` grant covers only
        its own path, never a descendant.
        """
        matching = [
            entry
            for entry in read_set.entries
            if path == entry.path or (entry.mode == "subtree" and path.startswith(entry.path))
        ]
        return tuple(ReadEntry(path, entry.visibilities, entry.mode) for entry in matching)


def register_read_handlers(
    engine: Engine,
    runtime: Runtime,
    *,
    planner: Planner | None = None,
    recorder: PlanRecorder | None = None,
    observer: StageObserver | None = None,
    results: ResultStore | None = None,
    costs: CostStore | None = None,
    clock: Callable[[], float] | None = None,
    collect_cost: bool = True,
    embedder: Embedder | None = None,
    judge: RelevanceJudge | None = None,
    judge_floor: float = JUDGE_RELEVANCE_FLOOR,
) -> ReadHandlers:
    """Wire ``get``, ``query``, ``refine``, ``provenance`` and ``cost`` onto ``engine``.

    The optional keyword arguments are the ``E-031``/``E-034``/``E-036`` seams so tests can inject a planner,
    recorder, observer, result store, cost store or monotonic clock without patching module state. ``results`` and
    ``costs`` default to the engine's own stores so every call through ``engine`` sees one shared view. ``embedder``
    is the deployment's query embedder (``V2-D98``): the engine owns the dense arm's query side, so a client sends
    text and receives hybrid recall; ``judge`` is the deployment's relevance judge (``V2-D104``), and its floor is
    the model's own measured operating point, not the engine's.
    """
    # `is not None`, not `or`: a store is a seam (it may carry an injected clock or capacity), and an empty one is
    # falsy, so `or` would silently swap it for the engine's own map.
    store = results if results is not None else engine.results
    cost_store = costs if costs is not None else engine.costs
    # One map or none: `Engine.call` resolves a `result_id` through `engine.results` while the handler issues into
    # whatever it was given, so a store that is not the engine's would split issue from resolve and refuse every
    # refine on a handle the engine just minted.
    engine.results = store
    engine.costs = cost_store
    handlers = ReadHandlers(
        runtime,
        planner=planner,
        recorder=recorder,
        observer=observer,
        results=store,
        costs=cost_store,
        clock=clock,
        collect_cost=collect_cost,
        embedder=embedder,
        judge=judge,
        judge_floor=judge_floor,
    )
    table: Mapping[str, Handler] = {
        "get": handlers.get,
        "stats": handlers.stats,
        "query": handlers.query,
        "refine": handlers.refine,
        "provenance": handlers.provenance,
        "cost": handlers.cost,
    }
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["Artifact", "CostReport", "QueryCost", "QueryResult", "ReadHandlers", "register_read_handlers"]
