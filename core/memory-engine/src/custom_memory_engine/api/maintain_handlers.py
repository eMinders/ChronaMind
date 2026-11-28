"""The ``purge_working`` handler (E-026; ``API_V2.md`` 4.7, ``V2-D47``, ``V2-D10``).

Same contract as :mod:`api.handlers`: reached only through :meth:`api.entry.Engine.call`, run as the caller under the
restricted role, static errors only. Before this runs the entry point has already established that the caller holds
the admin role within Tier C (a Tier B or non-admin Tier C credential is ``forbidden``) and that ``scope`` passes
``scope_writable`` (it lies in the caller's own ``/u/<caller>/`` namespace).

The handler must not break ``V2-D10``: a purge never removes answerability. What it does to an artifact whose evidence
is a working episode is rule PW-1 of :mod:`engine.purge`, the only place that decides it; this module adds nothing to
the rule. The answer is a :class:`~custom_memory_engine.engine.purge.PurgeReceipt`: counts only, no id, no path, no
content (``API_V2.md`` 1.1). A purge with nothing to purge, including a repeated one, answers zero counts.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Runtime, new_id
from custom_memory_engine.deletion.closure import current_deletion_epoch
from custom_memory_engine.engine import purge
from custom_memory_engine.engine.expiry import ExpiryReceipt
from custom_memory_engine.engine.index_maintenance import JOB_KIND, MaintenanceError, job_for, logical_index
from custom_memory_engine.profile import Profile
from custom_memory_engine.queue import enqueue
from custom_memory_engine.scope.paths import ScopeError, parse_scope
from custom_memory_engine.security.audit_guard import content_free_details


def _instant(value: object) -> datetime:
    """A caller-supplied ISO instant; a naive one is refused, because an expiry is a point in a time zone."""
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise EntryError("validation_error", "as_of_invalid") from None
    else:
        raise EntryError("validation_error", "as_of_invalid")
    if parsed.tzinfo is None:
        raise EntryError("validation_error", "as_of_must_carry_a_timezone")
    return parsed.astimezone(UTC)


async def belief_time(connection: AsyncConnection) -> datetime:
    """The engine's own clock (ρ): expiry is observed, never dated by the caller (``V2-D73``)."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT pg_catalog.transaction_timestamp()")
        row = await cursor.fetchone()
    return row[0] if row is not None and isinstance(row[0], datetime) else datetime.now(UTC)


class MaintainHandlers:
    """The Maintain handlers: ``purge_working``, ``expire`` and ``rebuild_index``, bound to one :class:`Runtime`."""

    def __init__(self, runtime: Runtime, profile: Profile | None = None) -> None:
        self.runtime = runtime
        # `expire` reads its numbers from here and nowhere else: `V2-D14` owns the *values*, the policy plumbing is
        # already in the profile, and a handler that invented a TTL would be the placeholder this phase removes.
        self.profile = profile

    async def purge_working(self, call: Call) -> purge.PurgeReceipt:
        try:
            scope = parse_scope(call.arguments.get("scope")).text
        except ScopeError:
            raise EntryError("validation_error", "scope_invalid") from None
        user = call.ctx.principal
        async with self.runtime.session(user) as connection:
            return await purge.purge_working(connection, user, scope, schema_name=self.runtime.schema_name)

    async def expire(self, call: Call) -> ExpiryReceipt:
        """Apply retention expiry to the abstractions of one scope: stamp ``expires_at``, destroy nothing.

        ``API_V2.md`` §4.7 says expiry is "recorded as a scope event, not a deletion of knowledge", and the schema
        agrees: ``abstraction_stats.expires_at`` is what ``staleness_inputs`` reads to derive the ``expired`` cause, so
        stamping it is exactly how a belief becomes stale (``V2-D24`` — never a stored flag). Nothing is deleted, which
        is why the contract's ``deletes`` flag is corrected to ``false`` (``V2-D97``): the previous declaration claimed
        a deletion the operation does not perform, and a guarantee nothing enforces is the defect this repository keeps
        finding.

        **Where the numbers come from.** Only ``profile.abstraction_expiry.days_by_facet``. With no profile, or a facet
        the profile gives no window, nothing expires and the receipt says so: ``V2-D14`` is open on the values, and
        this handler does not get to invent them. An abstraction expires ``valid_from + days``; that instant is what is
        stamped, so the stored value is a function of the row and the policy and never of the request.

        ``as_of`` narrows which rows are considered and may not be in the future: expiry is an observation of
        the clock,
        and letting a caller date it forward would let one request expire a whole workspace.
        """
        try:
            scope = parse_scope(call.arguments.get("scope")).text
        except ScopeError:
            raise EntryError("validation_error", "scope_invalid") from None
        user = call.ctx.principal
        async with self.runtime.session(user) as connection:
            now = await belief_time(connection)
            as_of = await self._as_of(call, connection, now)
            policy = {} if self.profile is None else dict(self.profile.abstraction_expiry.days_by_facet)
            if not policy:
                return ExpiryReceipt(expired=0, considered=0, recorded_at=now)
            expired, considered = await self._expire_scope(connection, user, scope, as_of, policy)
            await self._record_expiry(connection, user, scope, expired, considered)
            return ExpiryReceipt(expired=expired, considered=considered, recorded_at=now)

    async def _as_of(self, call: Call, connection: AsyncConnection, now: datetime) -> datetime:
        raw = call.arguments.get("as_of")
        if raw is None:
            return now
        instant = _instant(raw)
        if instant > now:
            raise EntryError("precondition_failed", "as_of_in_the_future")
        return instant

    async def _expire_scope(
        self,
        connection: AsyncConnection,
        user: str,
        scope: str,
        as_of: datetime,
        policy: Mapping[str, int],
    ) -> tuple[int, int]:
        """Stamp ``expires_at`` on the scope's eligible abstractions; returns ``(expired, considered)``.

        ``considered`` counts every inferred abstraction in the subtree, so a receipt of ``(0, 7)`` means "seven
        candidates, none past its window" rather than "nothing was there". The predicate uses
        ``pg_catalog.starts_with`` on the scope path — never ``LIKE`` — for the reason the scope layer gives: a pattern
        would make ``/u/alice/a/`` a prefix of ``/u/alice/ab/``.
        """
        # The policy is a `VALUES` list rather than two arrays joined by `unnest`: the multi-array form is not a
        # `pg_catalog` function this project can pin, and an unqualified call would bind to whatever `search_path` the
        # connection carries. Facet names and windows come from the profile (a closed vocabulary), and each is a
        # literal, so nothing here interpolates caller text.
        windows = sql.SQL(", ").join(
            sql.SQL("({}, {})").format(sql.Literal(facet), sql.Literal(int(days))) for facet, days in policy.items()
        )
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "WITH scoped AS ("
                    " SELECT v.id, v.um_facet, lower(v.event_range) AS believed_from"
                    " FROM {v} AS v JOIN {s} AS s ON s.version_id = v.id AND s.user_id = v.user_id"
                    " WHERE v.user_id = %s AND v.kind = 'abstraction' AND v.lifecycle = 'inferred'"
                    " AND pg_catalog.starts_with(v.scope_path, %s)),"
                    " eligible AS ("
                    " SELECT scoped.id,"
                    " scoped.believed_from + (policy.days * interval '1 day') AS expires"
                    " FROM scoped JOIN (VALUES {windows}) AS policy(facet, days)"
                    " ON policy.facet = scoped.um_facet)"
                    " UPDATE {s} AS s SET expires_at = eligible.expires"
                    " FROM eligible WHERE s.version_id = eligible.id AND s.user_id = %s"
                    " AND eligible.expires <= %s AND s.expires_at IS NULL"
                    " RETURNING s.version_id"
                ).format(
                    v=self.runtime.table("memory_version"),
                    s=self.runtime.table("abstraction_stats"),
                    windows=windows,
                ),
                (user, scope, user, as_of),
            )
            expired = len(await cursor.fetchall())
            await cursor.execute(
                sql.SQL(
                    "SELECT count(*) FROM {v} AS v JOIN {s} AS s ON s.version_id = v.id AND s.user_id = v.user_id"
                    " WHERE v.user_id = %s AND v.kind = 'abstraction' AND v.lifecycle = 'inferred'"
                    " AND pg_catalog.starts_with(v.scope_path, %s)"
                ).format(v=self.runtime.table("memory_version"), s=self.runtime.table("abstraction_stats")),
                (user, scope),
            )
            row = await cursor.fetchone()
        return expired, int(row[0]) if row else 0

    async def _record_expiry(
        self, connection: AsyncConnection, user: str, scope: str, expired: int, considered: int
    ) -> None:
        """One content-free scope event: counts only, never a path, an id or content (``V2-D10``)."""
        async with connection.cursor() as cursor:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                    "VALUES (%s, %s, %s, 'retention_expired', pg_catalog.transaction_timestamp(), %s)"
                ).format(a=self.runtime.table("audit_event")),
                (
                    new_id("audit"),
                    user,
                    new_id("ref"),
                    Jsonb(content_free_details({"expired": expired, "considered": considered})),
                ),
            )

    async def rebuild_index(self, call: Call) -> str:
        """Queue a rebuild of one logical index and return its job id (``API_V2.md`` §4.7, ``A-014``).

        The work itself is :mod:`engine.index_maintenance`'s, and which shape it needs is *its* decision, not this
        handler's: ``fts``/``vector`` must run ``REINDEX ... CONCURRENTLY`` outside any transaction, ``closure_hash``
        reseals a derived column inside one. This handler therefore does the only thing it can do inside a request —
        validate the name, capture the deletion epoch the rebuild is valid under, and enqueue — and answers immediately
        with the id, which is what the contract returns.
        """
        user = call.ctx.principal
        try:
            index = logical_index(call.arguments.get("index"))
        except MaintenanceError as exc:
            raise EntryError("validation_error", exc.code) from None
        async with self.runtime.session(user) as connection:
            epoch = await current_deletion_epoch(connection, user, schema_name=self.runtime.schema_name)
            job = job_for(
                user,
                index,
                job_id=new_id("job"),
                # One rebuild per request: `enqueue` needs an identity, and reusing one would make the second call a
                # replay of the first rather than a fresh rebuild of whatever the corpus looks like now.
                key=f"{JOB_KIND}:{index}:{new_id('k')}",
                deletion_epoch=epoch,
            )
            created = await enqueue(connection, job, schema_name=self.runtime.schema_name)
            return created.job.id


def register_maintain_handlers(engine: Engine, runtime: Runtime, profile: Profile | None = None) -> MaintainHandlers:
    """Wire ``purge_working``, ``expire`` and ``rebuild_index`` onto ``engine``."""
    handlers = MaintainHandlers(runtime, profile)
    table: Mapping[str, Handler] = {
        "purge_working": handlers.purge_working,
        "expire": handlers.expire,
        "rebuild_index": handlers.rebuild_index,
    }
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["MaintainHandlers", "ExpiryReceipt", "register_maintain_handlers"]
