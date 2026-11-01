"""Read a memory version's evidence as generalised sources (M4, E-013).

Retrieval with provenance: each stored source is a ``(source_type, source_ref, span, content_hash)`` reference plus
its evidence role. ``in_deletion_boundary`` is ``False`` for an ``external`` source, whose content the engine
cannot erase
(``D-ENG-04``); deletion receipts list those as exclusions.
"""

from dataclasses import dataclass

from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import tuple_row


class SourceReadError(InvalidParameterValue):
    """Static code (SQLSTATE 22023); never echoes an identifier."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class StoredSource:
    source_type: str
    source_ref: str
    span: str
    content_hash: str
    role: str

    @property
    def in_deletion_boundary(self) -> bool:
        return self.source_type == "turn"


async def fetch_sources(
    connection: AsyncConnection, user_id: str, version_id: str, *, schema_name: str = "cme_engine"
) -> tuple[StoredSource, ...]:
    """The evidence of ``version_id`` for ``user_id``, in a stable order. Empty when it has none or does not exist."""
    if (
        not isinstance(user_id, str)
        or not user_id.strip()
        or not isinstance(version_id, str)
        or not version_id.strip()
    ):
        raise SourceReadError("nonblank_user_id_and_version_id_required")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        context = await cursor.fetchone()
        if context is None or context[0] != user_id:
            raise InsufficientPrivilege("provenance_user_context_mismatch")
        await cursor.execute(
            sql.SQL(
                "SELECT source_type, source_ref, span, content_hash, evidence_role FROM {e} "
                "WHERE version_id = %s AND user_id = %s ORDER BY source_type, source_ref, span"
            ).format(e=sql.Identifier(schema_name, "memory_evidence")),
            (version_id, user_id),
        )
        return tuple(StoredSource(*row) for row in await cursor.fetchall())


__all__ = ["SourceReadError", "StoredSource", "fetch_sources"]
