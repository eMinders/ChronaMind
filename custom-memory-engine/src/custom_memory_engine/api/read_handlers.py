"""Read-path handlers: ``get`` and ``query`` (E-033 partial, wired by V2-D66 for E-021 AC4/AC5).

``API_V2.md`` §4.2 defines ``get`` (one artifact by id) and ``query`` (staged retrieval returning the four channels
of ``CHANNELS.md``). Both are owned by ``E-033``; that task is gated behind ``G2``, and the Project Owner authorized
this minimal landing early under ``V2-D66`` so the six coding-profile fixtures can be stored **and read back** through
the generic public API (issue #136 acceptance criteria 4 and 5).

**What is minimal here, and what stays with E-033.** Implemented: single-artifact retrieval with channel
classification; scope-filtered retrieval grouped into the four channels. Not implemented here, and still ``E-033``'s
obligation: channel **budgets** and ranking (``E-033``), the **cost** report (``E-036``), the temporal predicates
(``E-031``'s planner), ``refine`` (``E-034``), and staleness computation (``E-037``/``E-038``, which need the closure
hash that does not exist yet — this module passes ``never_stale``, the function ``channels.py`` documents for callers
with no closure hash).

**Why nothing below ``Engine.call`` skips authorisation.** The caller arrives already authorised for the *operation*;
what decides which **rows** are visible is row-level security, bound to the authenticated principal by
``Runtime.session``. :func:`scope.access.read_predicate` narrows further to the caller's resolved read set, so a
selector the caller was denied cannot widen the result even where a grant exists.
"""

from collections.abc import Mapping, Sequence
from typing import cast

import structlog
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.engine.channels import Channel, ChannelError, Verification, channel_of
from custom_memory_engine.model.axes import Kind, Lifecycle
from custom_memory_engine.scope.access import ReadEntry, ReadSet, Visibility, read_predicate, row_visible
from custom_memory_engine.scope.paths import ScopeError, parse_scope

logger = structlog.get_logger(__name__)

#: ``API_V2.md`` §4.2 declares ``resource_limit`` for "more than 16 selectors".
MAX_SELECTORS = 16
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
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
    """The four channels plus the artifacts refused as illegal tuples (``API_V2.md`` §4.2, minimal form)."""

    __slots__ = ("conflicts", "facts", "hypotheses", "procedures", "refused")

    def __init__(
        self,
        *,
        facts: Sequence[Artifact] = (),
        hypotheses: Sequence[Artifact] = (),
        procedures: Sequence[Artifact] = (),
        conflicts: Sequence[Artifact] = (),
        refused: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.facts = tuple(facts)
        self.hypotheses = tuple(hypotheses)
        self.procedures = tuple(procedures)
        self.conflicts = tuple(conflicts)
        self.refused = tuple(refused)

    def channel(self, name: str) -> tuple[Artifact, ...]:
        """The items in one channel, by the ``CHANNELS.md`` name."""
        if name not in {"facts", "hypotheses", "procedures", "conflicts"}:
            raise ValueError("unknown_channel")
        return cast(tuple[Artifact, ...], getattr(self, name))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, QueryResult):
            return NotImplemented
        return (self.facts, self.hypotheses, self.procedures, self.conflicts, self.refused) == (
            other.facts,
            other.hypotheses,
            other.procedures,
            other.conflicts,
            other.refused,
        )

    def __repr__(self) -> str:
        return (
            f"QueryResult(facts={len(self.facts)}, hypotheses={len(self.hypotheses)}, "
            f"procedures={len(self.procedures)}, conflicts={len(self.conflicts)}, refused={len(self.refused)})"
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

    async def query(self, call: Call) -> QueryResult:
        request = call.arguments.get("request")
        if not isinstance(request, Mapping):
            raise EntryError("validation_error", "request_required")
        limit = request.get("limit", DEFAULT_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise EntryError("validation_error", "limit_out_of_range")
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
        if effective_read_set.empty:
            return QueryResult()
        if len(effective_read_set.entries) > MAX_SELECTORS:
            raise EntryError("resource_limit", "too_many_selectors")
        user = call.ctx.principal
        async with self.runtime.session(user) as connection:
            rows = await self._rows(connection, effective_read_set, user, limit)
            ids = [str(row[0]) for row in rows]
            verifications = await self._verification_of(connection, user, ids)
            contradicted = await self._contradicted_ids(connection, user, ids)
        buckets: dict[str, list[Artifact]] = {"facts": [], "hypotheses": [], "procedures": [], "conflicts": []}
        refused: list[tuple[str, str]] = []
        for row in rows:
            identifier, kind, lifecycle, content, scope_path, visibility = row
            artifact_id = str(identifier)
            # defence in depth: the reference semantics must agree with the SQL predicate (V2-D45)
            if not row_visible(effective_read_set, scope_path, visibility):
                continue
            verification = verifications.get(artifact_id, "none")
            channel = _channel_for(str(kind), str(lifecycle), verification, artifact_id in contradicted)
            if channel is None:
                refused.append((artifact_id, "illegal_artifact_tuple"))
                continue
            if channel == "none":
                continue
            buckets[channel].append(
                Artifact(
                    artifact_id=artifact_id,
                    kind=str(kind),
                    lifecycle=str(lifecycle),
                    content=str(content),
                    scope_path=str(scope_path),
                    channel=channel,
                    verification=verification,
                )
            )
        return QueryResult(
            facts=buckets["facts"],
            hypotheses=buckets["hypotheses"],
            procedures=buckets["procedures"],
            conflicts=buckets["conflicts"],
            refused=refused,
        )

    async def _rows(
        self,
        connection: AsyncConnection,
        read_set: ReadSet,
        user: str,
        limit: int,
    ) -> list[tuple[object, ...]]:
        """Readable rows in ``read_set``, newest belief first, capped by ``limit``.

        The predicate is :func:`read_predicate`, the SQL twin of :func:`row_visible`: a ``private`` row matches only
        a selector whose path equals its scope (V2 §5 rule 4, threat T-06), so a root query does not surface a
        private row stored in a sub-scope; that row is read by querying its own scope.
        """
        predicate, params = read_predicate(read_set, "scope_path", "visibility")
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT id, kind, lifecycle, content, scope_path, visibility FROM {t} "
                    "WHERE user_id = %s AND kind IS NOT NULL AND lifecycle IS NOT NULL AND {} "
                    "ORDER BY lower(recorded_range) DESC NULLS LAST, id LIMIT %s"
                ).format(predicate, t=self.runtime.table("memory_version")),
                (user, *params, limit),
            )
            return list(await cursor.fetchall())

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
