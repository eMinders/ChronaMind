"""Content-free ``scope_read`` audit events for cross-scope reads (``ARCHITECTURE_V2.md`` 5, rule 6; ``E-011``).

A read that reaches outside the caller's own namespace, through a grant, leaves one audit row holding counts
only: how many selectors the read set had and how many of them lie outside the caller's namespace. It never
records a path, a principal, a grant id or any content, because audit rows outlive the memories they describe
(``security.audit_guard``). A read confined to the caller's own namespace records nothing.

Two writers, one shape:

* :func:`record_scope_read` takes an application :class:`~custom_memory_engine.scope.access.ReadSet` (the resolved
  selectors) and is what a read path that computed a read set calls;
* :class:`ConnectionScopeReads` is the ``api.entry.ScopeReadRecorder`` the public entry point uses. It receives the
  scope paths a call actually *resolved* outside the caller's namespace - not the selectors it was allowed to name -
  so the audit reflects reads that happened, and it counts each distinct path once.

Both write the same two counts, through the same guard, in the caller's own transaction and under the caller's own
identity. The runtime role has ``INSERT`` on ``audit_event`` (``provisioning.TABLE_GRANTS``) and no other privilege.
"""

from collections.abc import Sequence
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.scope.access import ReadSet
from custom_memory_engine.scope.paths import MAX_READ_SCOPE_PREFIXES, ScopeError, parse_scope, user_scope
from custom_memory_engine.security.audit_guard import content_free_details

SCOPE_READ_EVENT = "scope_read"


async def _assert_caller_context(connection: AsyncConnection, user_id: str) -> None:
    """The transaction's identity must be the reader: an audit row is never written under someone else's name."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
    if row is None or row[0] != user_id:
        raise InsufficientPrivilege("scope_audit_user_context_mismatch")


async def _write(connection: AsyncConnection, user_id: str, selector_count: int, cross: int, schema_name: str) -> str:
    event_id = f"audit-{uuid4().hex}"
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                "VALUES (%s, %s, %s, %s, pg_catalog.transaction_timestamp(), %s)"
            ).format(a=sql.Identifier(schema_name, "audit_event")),
            (
                event_id,
                user_id,
                uuid4().hex,
                SCOPE_READ_EVENT,
                Jsonb(content_free_details({"selector_count": selector_count, "cross_scope_count": cross})),
            ),
        )
    return event_id


async def record_scope_read(
    connection: AsyncConnection, user_id: str, read_set: ReadSet, *, schema_name: str = "cme_engine"
) -> str | None:
    """Record a cross-scope read; return the audit id, or ``None`` when the read stayed in the caller's namespace."""
    try:
        own = user_scope(user_id).text
    except ScopeError:
        raise InsufficientPrivilege("scope_audit_user_invalid") from None
    cross = sum(1 for entry in read_set.entries if not entry.path.startswith(own))
    if cross == 0:
        return None
    await _assert_caller_context(connection, user_id)
    return await _write(connection, user_id, len(read_set.entries), cross, schema_name)


class ConnectionScopeReads:
    """The entry point's ``ScopeReadRecorder``: one content-free row per call that read outside the caller's namespace.

    ``session`` is the caller-scoped transaction factory (``api.runtime.Runtime.session``), so the row is written as
    the caller, under the caller's row-level security, in the same transaction as the read it describes. A path that
    cannot be parsed fails closed: the read is refused rather than recorded unaccountably.
    """

    def __init__(self, session_factory: object, *, schema_name: str = "cme_engine") -> None:
        self._session = session_factory
        self.schema_name = schema_name

    async def record(self, principal: str, cross_scope_paths: Sequence[str]) -> None:
        own = user_scope(principal).text
        counted = {path for path in cross_scope_paths if not parse_scope(path).text.startswith(own)}
        if not counted:
            return
        if len(counted) > MAX_READ_SCOPE_PREFIXES:
            # The same declared limit the read set has: a call cannot smuggle an unbounded audit row past it.
            raise ScopeError("read_prefix_limit_exceeded")
        async with self._session(principal) as connection:  # type: ignore[attr-defined]
            await _assert_caller_context(connection, principal)
            await _write(connection, principal, len(counted), len(counted), self.schema_name)


__all__ = ["SCOPE_READ_EVENT", "ConnectionScopeReads", "record_scope_read"]
