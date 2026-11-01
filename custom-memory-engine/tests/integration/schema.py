"""The one way integration suites build the engine schema: every shipped migration, in order.

Suites used to read ``001_full_schema.sql`` (some also ``002``) themselves, so engine behaviour was
tested against a schema that production never runs, and a new migration went untested by them.
``apply_migrations`` reads ``migrate.MIGRATIONS``, so each new migration is covered automatically.
The caller's ``search_path`` must already name the target schema first.
"""

from typing import LiteralString, cast

from psycopg import AsyncConnection

from custom_memory_engine.schema import migrate


async def apply_migrations(conn: AsyncConnection, *, through: int | None = None) -> None:
    """Apply migrations ``1..through`` (default: all) into the current schema."""
    for version, filename in migrate.MIGRATIONS:
        if through is not None and version > through:
            break
        source = (migrate.SCHEMA_SQL.parent / filename).read_text(encoding="utf-8")
        await conn.execute(cast(LiteralString, source), prepare=False)
