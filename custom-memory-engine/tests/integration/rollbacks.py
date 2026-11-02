"""Published rollback scripts, composed newest-first, for tests that rebuild an older schema state."""

from pathlib import Path
from typing import LiteralString, cast

from psycopg import AsyncConnection

from custom_memory_engine.schema import migrate

ROLLBACK_DIR = Path(migrate.__file__).parent / "rollback"


def rollback_sql(*, from_version: int, to_version: int) -> str:
    """The rollback scripts that take a ``from_version`` schema down to ``to_version``, newest first.

    Each script is the operator's rollback for one migration (``schema/rollback/<filename>``); the runner never
    executes them. Composing them in reverse order is what an operator does to return to an earlier version.
    """
    filenames = dict(migrate.MIGRATIONS)
    return "\n".join(
        (ROLLBACK_DIR / filenames[version]).read_text(encoding="utf-8")
        for version in range(from_version, to_version, -1)
    )


async def adapt(conn: AsyncConnection, statement: str) -> LiteralString:
    """``statement`` with ``event_range`` spelled as the schema currently has it.

    Tests that roll the schema back below migration 9 (``valid_range`` -> ``event_range``) and then seed rows need the
    historical spelling; everything else uses the current one.
    """
    cursor = await conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'memory_version' AND column_name IN ('event_range', 'valid_range')"
    )
    row = await cursor.fetchone()
    column = row[0] if row else "event_range"
    return cast(LiteralString, statement.replace("event_range", column))


async def has_column(conn: AsyncConnection, table: str, column: str) -> bool:
    """Whether ``table.column`` exists in the current schema (tests that straddle a migration use it)."""
    cursor = await conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = %s AND column_name = %s",
        (table, column),
    )
    return await cursor.fetchone() is not None
