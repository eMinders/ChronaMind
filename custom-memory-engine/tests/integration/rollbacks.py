"""Published rollback scripts, composed newest-first, for tests that rebuild an older schema state."""

from pathlib import Path

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
