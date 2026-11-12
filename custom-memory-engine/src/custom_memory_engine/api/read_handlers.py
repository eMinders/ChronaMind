"""Read-path handlers: ``get`` and ``query``
(E-033 partial / V2-D66 / E-021 AC4-AC5; temporal modes E-031 / V2-D73).

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

**What is minimal here, and what stays with E-033.** Implemented: single-artifact retrieval with channel
classification; scope-filtered retrieval grouped into the four channels; the four temporal modes. Not implemented here,
and still ``E-033``'s obligation: channel **budgets** and ranking (``E-033``), the **cost** report (``E-036``),
the ``change`` mode (``E-033``), ``refine`` (``E-034``), and staleness computation (``E-037``/``E-038``, which need
the closure hash that does not exist yet — this module passes ``never_stale``, the function ``channels.py`` documents
for callers with no closure hash).

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
from custom_memory_engine.engine.channels import Channel, ChannelError, Verification, channel_of
from custom_memory_engine.engine.outcomes import stats_for as outcome_stats_for
from custom_memory_engine.engine.predicates import (
    TemporalQuery,
    TemporalQueryError,
    audit_as_of,
    current_state,
    fetch_scoped_rows,
    historical_as_of,
    historical_window,
    reference_instant,
)
from custom_memory_engine.engine.procedure_ranking import order_procedures
from custom_memory_engine.model.axes import Kind, Lifecycle
from custom_memory_engine.scope.access import ReadEntry, ReadSet, Visibility, read_predicate_named, row_visible
from custom_memory_engine.scope.paths import ScopeError, parse_scope

logger = structlog.get_logger(__name__)

#: ``API_V2.md`` §4.2 declares ``resource_limit`` for "more than 16 selectors".
MAX_SELECTORS = 16
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
#: ``API_V2.md`` §4.2 minus ``change``: E-031 wires the four temporal predicates; E-033 owns the diff.
TEMPORAL_MODES = frozenset({"current_state", "historical_as_of", "historical_window", "audit_as_of"})
_ALL_VISIBILITIES: frozenset[Visibility] = frozenset({"private", "project", "shared"})


class Artifact:
    """One stored artifact as ``get`` returns it (``API_V2.md`` §4.2 ``Artifact``)."""

    __slots__ = ("artifact_id", "channel", "content", "kind", "lifecycle", "scope_path", "verification")

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
    ) -> None:
        self.artifact_id = artifact_id
        self.kind = kind
        self.lifecycle = lifecycle
        self.content = content
        self.scope_path = scope_path
        self.channel = channel
        self.verification = verification

    def _key(self) -> tuple[object, ...]:
        return (
            self.artifact_id,
            self.kind,
            self.lifecycle,
            self.content,
            self.scope_path,
            self.channel,
            self.verification,
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
    """The four channels plus the artifacts refused as illegal tuples (``API_V2.md`` §4.2, minimal form).

    ``is_audit`` is ``True`` only for ``audit_as_of`` queries; ``TECHNICAL_SPECIFICATION.md`` 3.5 forbids
    using those results as answer context, so callers must check this flag before treating the result as one.
    """

    __slots__ = ("audit", "conflicts", "facts", "hypotheses", "is_audit", "procedures", "refused")

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
    ) -> None:
        self.facts = tuple(facts)
        self.hypotheses = tuple(hypotheses)
        self.procedures = tuple(procedures)
        self.conflicts = tuple(conflicts)
        self.refused = tuple(refused)
        self.audit = tuple(audit)
        self.is_audit = is_audit

    def channel(self, name: str) -> tuple[Artifact, ...]:
        """The items in one channel, by the ``CHANNELS.md`` name."""
        if name not in {"facts", "hypotheses", "procedures", "conflicts"}:
            raise ValueError("unknown_channel")
        return cast(tuple[Artifact, ...], getattr(self, name))

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
        ) == (
            other.facts,
            other.hypotheses,
            other.procedures,
            other.conflicts,
            other.refused,
            other.audit,
            other.is_audit,
        )

    def __repr__(self) -> str:
        audited = f", audit={len(self.audit)}" if self.is_audit else ""
        return (
            f"QueryResult(facts={len(self.facts)}, hypotheses={len(self.hypotheses)}, "
            f"procedures={len(self.procedures)}, conflicts={len(self.conflicts)}, "
            f"refused={len(self.refused)}{audited})"
        )


def _channel_for(kind: str, lifecycle: str, verification: str, open_contradiction: bool) -> Channel | None:
    """The channel of one stored row, or ``None`` when the tuple is not legal (reported, never rendered)."""
    try:
        return channel_of(
            cast(Kind, kind),
            cast(Lifecycle, lifecycle),
            cast(Verification, verification),
            False,
            open_contradiction,
        )
    except ChannelError:
        return None


class ReadHandlers:
    """``get`` and ``query``, bound to one :class:`Runtime`."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

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
        channel = _channel_for(str(kind), str(lifecycle), verification, contradicted)
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
        )

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

    def _build_temporal_query(
        self, user: str, mode: str, request: Mapping[str, object], reference_time: datetime
    ) -> TemporalQuery:
        """Parse the fields ``mode`` needs into a :class:`TemporalQuery`.

        ``reference_time`` is never taken from the caller: belief time is the database clock (``I4``), and a
        caller-chosen reference would let a request read a belief the engine never held at that instant.
        """
        target = self._instant_or_none(request, "target_time")
        belief = self._instant_or_none(request, "belief_time")
        start = self._instant_or_none(request, "window_start")
        end = self._instant_or_none(request, "window_end")
        try:
            if mode == "current_state":
                return current_state(user, reference_time)
            if mode == "historical_as_of":
                if target is None:
                    raise EntryError("validation_error", "target_time_required")
                return historical_as_of(user, target, reference_time)
            if mode == "historical_window":
                if start is None:
                    raise EntryError("validation_error", "window_start_required")
                if end is None:
                    raise EntryError("validation_error", "window_end_required")
                return historical_window(user, start, end, reference_time)
            if mode == "audit_as_of":
                if target is None:
                    raise EntryError("validation_error", "target_time_required")
                if belief is None:
                    raise EntryError("validation_error", "belief_time_required")
                return audit_as_of(user, target, belief)
        except TemporalQueryError as exc:
            raise EntryError("validation_error", exc.code) from None
        except ValueError:
            # a TemporalQuery field validator rejected the combination; the code is a static literal
            raise EntryError("validation_error", "temporal_parameter_mismatch") from None
        raise EntryError("validation_error", "mode_not_supported")

    async def _temporal_query(
        self, call: Call, mode: str, request: Mapping[str, object], effective_read_set: ReadSet, limit: int
    ) -> QueryResult:
        """Run one temporal mode through the engine's predicate and route the rows into channels.

        The temporal filter and the caller's read predicate are applied in a single statement, so ``limit`` counts
        rows the caller may actually read. ``audit_as_of`` results are returned in ``audit`` and never in a channel:
        ``TECHNICAL_SPECIFICATION.md`` 3.5 forbids using them as answer context, and ``channel_of`` maps a retired
        artifact to ``none``, so a channel-routed audit would silently drop exactly the rows an audit wants.
        """
        user = call.ctx.principal
        is_audit = mode == "audit_as_of"
        if is_audit and call.ctx.tier != "C":
            raise EntryError("forbidden")  # API_V2.md §4.2: audit_as_of is Tier C only
        read_filter, read_parameters = read_predicate_named(effective_read_set, "m.scope_path", "m.visibility")
        try:
            async with self.runtime.session(user) as connection:
                # the reference instant is the clock writes record against, read inside this transaction (I4)
                reference_time = await reference_instant(connection)
                tquery = self._build_temporal_query(user, mode, request, reference_time)
                rows = await fetch_scoped_rows(
                    connection,
                    tquery,
                    read_filter,
                    read_parameters,
                    limit=limit,
                    schema_name=self.runtime.schema_name,
                )
                ids = [row.id for row in rows]
                verifications = await self._verification_of(connection, user, ids)
                contradicted = await self._contradicted_ids(connection, user, ids)
                # E-027: outcome counters for the procedure rows, fetched in one statement beside the other
                # per-row metadata. Only procedures have them, so asking for every id is a wasted round trip at
                # best; asking only for procedure ids keeps the read proportional to what is ranked.
                stats = await outcome_stats_for(
                    connection,
                    user_id=user,
                    procedure_ids=[row.id for row in rows if row.kind == "procedure"],
                    schema_name=self.runtime.schema_name,
                )
        except InsufficientPrivilege as exc:
            raise EntryError("forbidden") from exc
        except TemporalQueryError as exc:
            raise EntryError("validation_error", exc.code) from exc

        buckets: dict[str, list[Artifact]] = {"facts": [], "hypotheses": [], "procedures": [], "conflicts": []}
        audited: list[Artifact] = []
        refused: list[tuple[str, str]] = []
        for row in rows:
            # Defence in depth (V2-D45): the reference semantics must agree with the SQL predicate. The statement
            # above already filtered, so this drops nothing in a correct build -- which is exactly what a control
            # breaks, and why the check stays.
            if not row_visible(effective_read_set, row.scope_path, row.visibility):
                continue
            verification = verifications.get(row.id, "none")
            channel = _channel_for(row.kind, row.lifecycle, verification, row.id in contradicted)
            if channel is None:
                refused.append((row.id, "illegal_artifact_tuple"))
                continue
            artifact = Artifact(
                artifact_id=row.id,
                kind=row.kind,
                lifecycle=row.lifecycle,
                content=row.content,
                scope_path=row.scope_path,
                channel=channel,
                verification=verification,
            )
            if is_audit:
                audited.append(artifact)
                continue
            if channel == "none":
                continue
            buckets[channel].append(artifact)
        if is_audit:
            return QueryResult(audit=audited, refused=refused, is_audit=True)
        # E-027 (V2-D75): procedures are ordered by recorded outcome, then recency, then id. This runs **after**
        # channel routing, so it cannot move an item between channels (`CHANNELS.md`, `I1'`): a contested or retired
        # procedure never reached this bucket, so no statistic can promote it. Other kinds keep the temporal read's
        # own order untouched.
        if buckets["procedures"]:
            by_id = {artifact.artifact_id: artifact for artifact in buckets["procedures"]}
            buckets["procedures"] = [by_id[procedure_id] for procedure_id in order_procedures(by_id, stats)]
        return QueryResult(
            facts=buckets["facts"],
            hypotheses=buckets["hypotheses"],
            procedures=buckets["procedures"],
            conflicts=buckets["conflicts"],
            refused=refused,
        )

    async def query(self, call: Call) -> QueryResult:
        request = call.arguments.get("request")
        if not isinstance(request, Mapping):
            raise EntryError("validation_error", "request_required")
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
        return await self._temporal_query(call, str(mode), request, effective_read_set, limit)

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


def register_read_handlers(engine: Engine, runtime: Runtime) -> ReadHandlers:
    """Wire ``get`` and ``query`` onto ``engine``."""
    handlers = ReadHandlers(runtime)
    table: Mapping[str, Handler] = {
        "get": handlers.get,
        "query": handlers.query,
    }
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["Artifact", "QueryResult", "ReadHandlers", "register_read_handlers"]
