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

``mode='change'`` is deferred to E-033's diff operator. Every temporal read routes through
:func:`engine.predicates.fetch_scoped_rows`, which applies the temporal predicate and the caller's read predicate in
**one** statement, then feeds the same channel-classification pipeline.

**Staged retrieval (E-031, ``V2-D11``).** ``query`` now splits into the two halves ``ARCHITECTURE_V2.md`` §9 draws:
:func:`~.engine.planning.plan_retrieval` is a **pure** function producing a
:class:`~.engine.planning.Plan`, and :meth:`ReadHandlers.execute_plan` runs it. The plan names the index, the ordered
filters, the expansion intent, the route (``channels`` or ``audit``) and the five stage names ``E-036`` measures; it
names **no** channel membership or ranking. The request limit bounds candidate generation. E-032 follows associations
from those candidates and filters each reached row as it enters expansion (``V2-D76``).

**Assembly (E-033).** Query results now pass through the pure four-channel assembler, with per-channel token budgets,
reported refusals/truncation, coverage and assembly cost. Each returned artifact carries a provenance bundle.
``get`` returns its bundle; ``provenance`` traverses readable lineage to a bounded depth and stops at opaque sources.
``change`` remains deliberately unwired: the existing decision record does not define the diff key or its row
outcomes, so the mode refusal remains until that contract gap is decided. Per-stage collection beyond assembly is
``E-036``'s responsibility; ``refine`` remains ``E-034``'s.

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

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast

import structlog
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Runtime
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
from custom_memory_engine.engine.expansion import expand_associations
from custom_memory_engine.engine.outcomes import stats_for as outcome_stats_for
from custom_memory_engine.engine.planning import (
    ASSOCIATION_EXPANSION,
    PROCEDURE_APPLICABILITY,
    Intent,
    Plan,
    Planner,
    PlanningError,
    PlanRecorder,
    RuleBasedPlanner,
    StageObserver,
    observe_nothing,
    plan_retrieval,
    record_nothing,
)
from custom_memory_engine.engine.predicates import (
    QueryMode,
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
    bundles_for_artifacts,
    provenance_view,
    sources_for_artifacts,
)
from custom_memory_engine.engine.staleness import ClosureCache, staleness_inputs
from custom_memory_engine.model.axes import Kind, Lifecycle
from custom_memory_engine.scope.access import (
    ReadEntry,
    ReadSet,
    SelectorMode,
    Visibility,
    read_predicate_named,
    row_visible,
)
from custom_memory_engine.scope.paths import ScopeError, parse_scope
from custom_memory_engine.scope.provenance import ProvenanceReference, ProvenanceSource

logger = structlog.get_logger(__name__)

#: ``API_V2.md`` §4.2 declares ``resource_limit`` for "more than 16 selectors".
MAX_SELECTORS = 16
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
#: ``API_V2.md`` §4.2 minus ``change``: E-031 wires the four temporal predicates; E-033 owns the diff.
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


class QueryResult:
    """The four channels, explicit per-channel coverage and assembly cost (``API_V2.md`` §4.2).

    ``is_audit`` is ``True`` only for ``audit_as_of`` queries; ``TECHNICAL_SPECIFICATION.md`` 3.5 forbids
    using those results as answer context, so callers must check this flag before treating the result as one.
    """

    __slots__ = (
        "audit", "conflicts", "coverage", "cost", "facts", "hypotheses", "is_audit", "procedures", "refused"
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
    ) -> None:
        self.facts = tuple(facts)
        self.hypotheses = tuple(hypotheses)
        self.procedures = tuple(procedures)
        self.conflicts = tuple(conflicts)
        self.refused = tuple(refused)
        self.audit = tuple(audit)
        self.is_audit = is_audit
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
            other.refused,
            other.audit,
            other.is_audit,
            other.coverage,
            other.cost,
        )

    def __repr__(self) -> str:
        audited = f", audit={len(self.audit)}" if self.is_audit else ""
        return (
            f"QueryResult(facts={len(self.facts)}, hypotheses={len(self.hypotheses)}, "
            f"procedures={len(self.procedures)}, conflicts={len(self.conflicts)}, "
            f"refused={len(self.refused)}{audited})"
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


class ReadHandlers:
    """Read operations bound to one :class:`Runtime`."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        planner: Planner | None = None,
        recorder: PlanRecorder | None = None,
        observer: StageObserver | None = None,
    ) -> None:
        self.runtime = runtime
        # E-031: the model seam, defaulted to the rule-based planner and therefore unused (criterion 9). The recorder
        # and observer are the two things E-036 attaches to; both default to a no-op, so production behaviour is
        # unchanged unless something opts in.
        self.planner: Planner = planner if planner is not None else RuleBasedPlanner()
        self.recorder: PlanRecorder = recorder if recorder is not None else record_nothing
        self.observer: StageObserver = observer if observer is not None else observe_nothing
        # E-038 (V2-D24): the closure-hash cache the read path derives `stale` through. One per handler, so repeated
        # reads of an unchanged abstraction hash its closure once; the deletion token invalidates it (F6).
        self.closure_cache = ClosureCache()

    # -- shared reads ------------------------------------------------------------------------------------------

    async def _verification_of(self, connection: AsyncConnection, user: str, ids: Sequence[str]) -> dict[str, str]:
        """Recorded verification status of abstractions, from ``ABSTRACTION_STATS`` (``I14``).

        Only an abstraction carries one: ``CHANNELS.md`` §1.1 makes ``verification='none'`` the only legal value for
        a grounded artifact, and an abstraction is never grounded (``V2-D48`` C-P1).
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
        """Which of ``ids`` is an endpoint of an open ``contradicts`` edge (``V2-D48`` C-P4).

        ``open_contradiction`` holds when *both* endpoints are grounded and the edge came from the grounding policy
        or a Tier C caller. The edge records that decision in ``origin``, so this reads a recorded fact rather than
        re-deriving the rule.
        """
        if not ids:
            return set()
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
            found: set[str] = set()
            for from_id, to_id in await cursor.fetchall():
                found.update((str(from_id), str(to_id)))
        return found

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
            identifier, kind, lifecycle, content, scope_path, visibility = row
            if not row_visible(call.ctx.read_set, scope_path, visibility):
                raise EntryError("forbidden")
            verification = (await self._verification_of(connection, user, [str(identifier)])).get(
                str(identifier), "none"
            )
            contradicted = str(identifier) in await self._contradicted_ids(connection, user, [str(identifier)])
            stale = str(identifier) in await self._stale_ids(connection, user, {str(identifier): verification})
            shape: ProcedureShape | None = None
            if kind == "procedure":
                shapes = await shapes_for(
                    connection, user_id=user, procedure_ids=[str(identifier)], schema_name=self.runtime.schema_name
                )
                shape = shapes.get(str(identifier), NO_SHAPE)
            provenance = (
                await bundles_for_artifacts(
                    connection,
                    user,
                    [str(identifier)],
                    read_set=call.ctx.read_set,
                    key=self.runtime.provenance_key,
                    schema_name=self.runtime.schema_name,
                )
            ).get(str(identifier), ())
            if not provenance:
                raise EntryError("internal_error")
        channel = _channel_for(str(kind), str(lifecycle), verification, contradicted, stale)
        if channel is None or channel == "none":
            raise EntryError("forbidden")  # a retired or illegal tuple is not rendered
        return Artifact(
            artifact_id=str(identifier),
            kind=str(kind),
            lifecycle=str(lifecycle),
            content=str(content),
            scope_path=str(scope_path),
            channel=channel,
            verification=verification,
            shape=shape,
            provenance=provenance,
        )

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
                if not view.edges:
                    raise EntryError("internal_error")
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
        if mode == "historical_window":
            if start is None:
                raise EntryError("validation_error", "window_start_required")
            if end is None:
                raise EntryError("validation_error", "window_end_required")
        if mode == "audit_as_of" and belief is None:
            raise EntryError("validation_error", "belief_time_required")
        try:
            return Intent(
                mode=cast(QueryMode, mode),
                limit=limit,
                target_time=target,
                belief_time=belief,
                window_start=start,
                window_end=end,
                scope=scope,
            )
        except PlanningError as exc:
            raise EntryError("validation_error", exc.code) from None

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
            held = any(
                wanted <= entry.visibilities
                and (scope.path == entry.path or (entry.mode == "subtree" and scope.path.startswith(entry.path)))
                and (scope.mode == "exact" or entry.mode == "subtree")
                for entry in read_set.entries
            )
            if not held:
                raise EntryError("forbidden")
            entries.append(ReadEntry(scope.path, cast(frozenset[Visibility], wanted), cast(SelectorMode, scope.mode)))
        return ReadSet(tuple(entries), ())

    async def execute_plan(self, call: Call, plan: Plan, *, budgets: ChannelBudgets | None = None) -> QueryResult:
        """Run ``plan`` for ``call``: the execution half of ``query``, separate from planning (``E-031`` W2).

        Stages, in plan order, each inside the observer's boundary (``E-036`` measures these):

        * ``candidate_generation`` -- in: the plan's filters and row limit; out: ``ScopedRow``s. The temporal
          predicate, the typed-artifact guard and the read predicate run in **one** statement before ``LIMIT``, so the
          limit counts rows the caller may read (``V2-D73``).
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
        source_map: Mapping[str, tuple[ProvenanceSource, ...]] = {}
        bounds: dict[str, Bound] = {}
        support_summaries: dict[str, str] = {}
        try:
            async with self.runtime.session(user) as connection:
                with self.observer("candidate_generation"):
                    # the reference instant is the clock writes record against, read inside this transaction (I4)
                    reference_time = await reference_instant(connection)
                    tquery = self._temporal_query_for(plan, user, reference_time)
                    rows = await fetch_scoped_rows(
                        connection,
                        tquery,
                        read_filter,
                        read_parameters,
                        limit=plan.rows,
                        schema_name=self.runtime.schema_name,
                    )
                with self.observer("expansion"):
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
                with self.observer("filter_rerank"):
                    ids = [row.id for row in rows]
                    verifications = await self._verification_of(connection, user, ids)
                    contradicted = await self._contradicted_ids(connection, user, ids)
                    # E-038-F5: derived staleness on every answer route; never on an audit (E-038-F3)
                    stale = set() if is_audit else await self._stale_ids(connection, user, verifications)
                    # E-027: outcome counters for the procedure rows, fetched in one statement beside the other
                    # per-row metadata. Only procedures have them, so asking for every id is a wasted round trip at
                    # best; asking only for procedure ids keeps the read proportional to what is ranked.
                    procedure_ids = [row.id for row in rows if row.kind == "procedure"]
                    stats = await outcome_stats_for(
                        connection, user_id=user, procedure_ids=procedure_ids, schema_name=self.runtime.schema_name
                    )
                    # E-030: each procedure's preconditions and steps, in one statement beside its counters
                    shapes = await shapes_for(
                        connection, user_id=user, procedure_ids=procedure_ids, schema_name=self.runtime.schema_name
                    )
                    # Defence in depth (V2-D45): the reference semantics must agree with the SQL predicate. The
                    # statement above already filtered, so this drops nothing in a correct build -- which is exactly
                    # what a control breaks, and why the check stays.
                    rows = tuple(
                        row for row in rows if row_visible(effective_read_set, row.scope_path, row.visibility)
                    )
                    # E-030 (criterion 8): the plan's applicability filter, **before** routing. It only removes a
                    # non-applicable procedure; every survivor reaches `channel_of` unchanged, so membership cannot
                    # move. A procedure stored without preconditions is always applicable (the empty conjunction).
                    if PROCEDURE_APPLICABILITY in plan.filters:
                        rows = await self._applicable_rows(connection, user, plan, rows, shapes)
                    if not is_audit:
                        source_map = await sources_for_artifacts(
                            connection, user, [row.id for row in rows], schema_name=self.runtime.schema_name
                        )
                        bounds, support_summaries = await self._bounds_of(
                            connection, user, [row.id for row in rows if row.kind == "abstraction"]
                        )
        except InsufficientPrivilege as exc:
            raise EntryError("forbidden") from exc
        except TemporalQueryError as exc:
            raise EntryError("validation_error", exc.code) from exc

        with self.observer("assemble"):
            if is_audit:
                audited: list[Artifact] = []
                refused: list[tuple[str, str]] = []
                for row in rows:
                    verification = verifications.get(row.id, "none")
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
                return QueryResult(audit=audited, refused=refused, is_audit=True)

            rows_by_id = {row.id: row for row in rows}
            views: list[ArtifactView] = []
            for row in rows:
                counterparts = tuple(
                    ConflictSide(
                        other_id,
                        cast(Lifecycle, rows_by_id[other_id].lifecycle),
                        rows_by_id[other_id].content,
                    )
                    for other_id in sorted(contradicted.intersection(rows_by_id) - {row.id})
                )
                views.append(
                    ArtifactView(
                        artifact_id=row.id,
                        kind=cast(Kind, row.kind),
                        lifecycle=cast(Lifecycle, row.lifecycle),
                        verification=cast(Verification, verifications.get(row.id, "none")),
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
                    verification=verifications.get(artifact_id, "none"),
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
            return QueryResult(
                facts=facts,
                hypotheses=hypotheses,
                procedures=procedures,
                conflicts=conflicts,
                refused=[(item.artifact_id, item.reason) for item in assembled.refused],
                coverage=assembled.coverage,
                cost=assembled.cost,
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
        if mode == "change":
            raise EntryError("validation_error", "mode_change_not_wired")  # E-033's diff operator
        if mode not in TEMPORAL_MODES:
            raise EntryError("validation_error", "mode_invalid")
        if "reference_time" in request:
            raise EntryError("validation_error", "reference_time_is_the_engine_clock")  # I4
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
        if effective_read_set.empty:
            return QueryResult(is_audit=mode == "audit_as_of")
        if len(effective_read_set.entries) > MAX_SELECTORS:
            raise EntryError("resource_limit", "too_many_selectors")
        intent = self._intent_for(str(mode), request, limit, requested_path)
        with self.observer("plan"):
            try:
                plan = plan_retrieval(intent, effective_read_set, planner=self.planner)
            except PlanningError as exc:
                raise EntryError("validation_error", exc.code) from None
        self.recorder(plan)
        return await self.execute_plan(call, plan, budgets=budgets)

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
) -> ReadHandlers:
    """Wire ``get``, ``query`` and ``provenance`` onto ``engine``.

    The three keyword arguments are ``E-031``'s seams and are all optional, so the production wiring in
    ``api/wiring.py`` is unchanged: the rule-based planner, no recording, no measurement.
    """
    handlers = ReadHandlers(runtime, planner=planner, recorder=recorder, observer=observer)
    table: Mapping[str, Handler] = {
        "get": handlers.get,
        "query": handlers.query,
        "provenance": handlers.provenance,
    }
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["Artifact", "QueryResult", "ReadHandlers", "register_read_handlers"]
