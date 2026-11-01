"""The one way integration suites build the engine schema: every shipped migration, in order.

Suites used to read ``001_full_schema.sql`` (some also ``002``) themselves, so engine behaviour was
tested against a schema that production never runs, and a new migration went untested by them.
``migrate.migration_scripts`` follows ``migrate.MIGRATIONS``, so each new migration is covered
automatically. The caller's ``search_path`` must already name the target schema first.
"""

from typing import LiteralString, cast

from psycopg import AsyncConnection

from custom_memory_engine.schema.migrate import migration_scripts


async def apply_migrations(conn: AsyncConnection, *, through: int | None = None) -> None:
    """Apply migrations ``1..through`` (default: all) into the current schema."""
    for _, source in migration_scripts(through=through):
        await conn.execute(cast(LiteralString, source), prepare=False)
