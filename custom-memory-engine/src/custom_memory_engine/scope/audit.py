"""Content-free ``scope_read`` audit events for cross-scope reads (``ARCHITECTURE_V2.md`` 5, rule 6; ``E-011``).

A read that reaches outside the caller's own namespace, through a grant, leaves one audit row holding counts
only: how many selectors the read set had and how many of them lie outside the caller's namespace. It never
records a path, a principal, a grant id or any content, because audit rows outlive the memories they describe
(``security.audit_guard``). A read confined to the caller's own namespace records nothing.
"""

from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.scope.access import ReadSet
from custom_memory_engine.scope.paths import ScopeError, user_scope
from custom_memory_engine.security.audit_guard import content_free_details


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
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("scope_audit_user_context_mismatch")
        event_id = f"audit-{uuid4().hex}"
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                "VALUES (%s, %s, %s, 'scope_read', pg_catalog.transaction_timestamp(), %s)"
            ).format(a=sql.Identifier(schema_name, "audit_event")),
            (
                event_id,
                user_id,
                uuid4().hex,
                Jsonb(content_free_details({"selector_count": len(read_set.entries), "cross_scope_count": cross})),
            ),
        )
    return event_id


__all__ = ["record_scope_read"]
