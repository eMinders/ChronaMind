"""The whole migration chain as one script, for fixtures that build a schema without the runner.

Older integration fixtures read ``001_full_schema.sql`` only, so they never saw a V2 column or trigger. They now read
the current chain instead, which makes the v1 regression wall run on the schema the engine actually targets. The
runner-based tests (``test_migrations.py`` and friends) are unaffected: they exercise the runner itself.
"""

from custom_memory_engine.schema import migrate


class CurrentSchema:
    """Duck-types the ``Path.read_text`` call the fixtures make on their former ``SCHEMA_SQL`` path."""

    def read_text(self, encoding: str = "utf-8") -> str:
        return "\n".join(
            migrate.SCHEMA_SQL.with_name(filename).read_text(encoding=encoding) for _, filename in migrate.MIGRATIONS
        )
