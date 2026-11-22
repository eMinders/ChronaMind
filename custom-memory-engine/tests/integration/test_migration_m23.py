"""Migration 23: language-agnostic lexical indexing and multilingual embedding slots (``V2-D86``, W3.5).

Checked against the REAL migrated schema, by direct SQL, because a replay harness, a backfill or an operator writes
without going through a handler. The properties owned here are the ones a Python convention cannot own: which
tokeniser the stored column uses, which dimensions a slot can index, and whether a row can fill two slots at once.
"""

from datetime import UTC, datetime
from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, DataException

from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture; seeds u1 and u2)

pytestmark = pytest.mark.integration
M23 = 23
T0 = datetime(2026, 3, 10, tzinfo=UTC)


def literal(values: list[float]) -> str:
    return "[" + ",".join(repr(value) for value in values) + "]"


def unit(dimensions: int, axis: int = 0) -> str:
    values = [0.0] * dimensions
    values[axis] = 1.0
    return literal(values)


async def claim(conn: AsyncConnection, version_id: str, content: str, *, user: str = "u1") -> None:
    """One asserted claim with its evidence, written directly: content is immutable once written (``guard_update``)."""
    session, turn_id = f"s-m23-{user}", f"t-m23-{user}"
    await conn.execute(
        "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, clock_now()) ON CONFLICT DO NOTHING",
        (session, user),
    )
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, %s, %s, 1, 'user', %s, clock_now()) ON CONFLICT DO NOTHING",
        (turn_id, session, user, content),
    )
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
        "content, event_range, recorded_range, layer, valid_from_basis, assertion_mode, extractor_version, "
        "scope_path, kind, lifecycle, retention) "
        "VALUES (%s, %s, %s, 'active', 'self', 'statement', false, %s, tstzrange(%s, NULL), "
        "tstzrange(clock_now(), NULL),"
        "'semantic', 'inferred', 'asserted', 'test', %s, 'claim', 'asserted', 'durable')",
        (version_id, version_id, user, content, T0, f"/u/{user}/"),
    )
    await conn.execute(
        "INSERT INTO memory_evidence (version_id, user_id, turn_id, evidence_span, evidence_role) "
        "VALUES (%s, %s, %s, %s, 'user')",
        (version_id, user, turn_id, content),
    )


async def test_the_stored_lexical_column_uses_the_language_free_configuration(database) -> None:  # noqa: F811
    """``'simple'`` lowercases and splits; it does not stem, so it cannot discard a morphology."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I moved to Berlin")
        # English stemming would reduce "moved" to "move" and match "moving"; `simple` matches only the exact lexeme
        assert await rows(
            conn,
            "SELECT 1 FROM memory_version WHERE id = 'v1' "
            "AND content_fts_simple @@ websearch_to_tsquery('simple', 'moved')",
        )
        assert not await rows(
            conn,
            "SELECT 1 FROM memory_version WHERE id = 'v1' "
            "AND content_fts_simple @@ websearch_to_tsquery('simple', 'moving')",
        )


async def test_arabic_and_cjk_text_are_tokenised_rather_than_discarded(database) -> None:  # noqa: F811
    """The defect this migration removes: an English configuration on non-English content matches noise."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "ar", "انتقلت إلى برلين الأسبوع الماضي")
        assert await rows(
            conn,
            "SELECT 1 FROM memory_version WHERE id = 'ar' "
            "AND content_fts_simple @@ websearch_to_tsquery('simple', 'برلين')",
        )
        await claim(conn, "cjk", "我家住在柏林")
        # a CJK "word" is a run of characters with no separators: the trigram arm is what finds a substring of it
        assert await rows(conn, "SELECT 1 FROM memory_version WHERE id = 'cjk' AND content ILIKE %s", ("%住在%",))


async def test_the_trigram_index_exists_and_serves_a_substring_predicate(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "Rerun the focused suite")
        await conn.execute("SET LOCAL enable_seqscan = off")
        plan = await rows(
            conn, "EXPLAIN (COSTS OFF) SELECT 1 FROM memory_version WHERE content ILIKE %s", ("%focus%",)
        )
        assert any("trgm" in str(line[0]) for line in plan)


async def test_a_multilingual_slot_stores_a_vector_of_its_own_width(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I moved to Berlin")
        await conn.execute(
            "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding_1024) "
            "VALUES ('v1', 'BAAI/bge-m3', 'u1', %s::halfvec(1024))",
            (unit(1024),),
        )
        assert await rows(
            conn, "SELECT embedding_model, vector_dims(embedding_1024) FROM memory_embedding WHERE version_id = 'v1'"
        ) == [("BAAI/bge-m3", 1024)]


async def test_a_row_must_fill_exactly_one_slot(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I moved to Berlin")
        with pytest.raises(CheckViolation):
            # the inner block is a SAVEPOINT, so the refusal does not poison the outer transaction
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding, embedding_1024) "
                    "VALUES ('v1', 'both', 'u1', %s::vector(384), %s::halfvec(1024))",
                    (unit(384), unit(1024)),
                )


async def test_the_slot_and_the_vector_width_cannot_disagree(database) -> None:  # noqa: F811
    """A 768-wide vector in the 1024 slot would be unsearchable by the arm that owns the slot."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I moved to Berlin")
        with pytest.raises((CheckViolation, DataException)):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding_1024) "
                    "VALUES ('v1', 'm', 'u1', %s::halfvec(768))",
                    (unit(768),),
                )


async def test_the_legacy_384_slot_still_accepts_the_vectors_it_always_held(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I moved to Berlin")
        await conn.execute(
            "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding) "
            "VALUES ('v1', 'BAAI/bge-small-en-v1.5', 'u1', %s::vector(384))",
            (unit(384),),
        )
        assert await rows(conn, "SELECT vector_dims(embedding) FROM memory_embedding WHERE version_id = 'v1'") == [
            (384,)
        ]


async def test_rollback_restores_version_22_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        before = await runner._catalog(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M23 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M23 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]
        assert before != recorded[0][0]
        for column in ("embedding_768", "embedding_1024", "embedding_1536", "embedding_3072"):
            assert not await rows(
                conn,
                "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = 'memory_embedding' AND column_name = %s",
                (column,),
            )
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'memory_version' AND column_name = 'content_fts_simple'",
        )
    assert (await runner.status()).version == M23 - 1
    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION


async def test_rolling_back_discards_multilingual_vectors_but_keeps_the_legacy_ones(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        # one transaction: the deferred provenance trigger fires at commit, so each claim and its evidence must commit
        # together (the engine's own write path does exactly this)
        async with conn.transaction():
            await claim(conn, "legacy", "I moved to Berlin")
            await claim(conn, "multi", "My manager approved the roadmap")
            await conn.execute(
                "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding) "
                "VALUES ('legacy', 'BAAI/bge-small-en-v1.5', 'u1', %s::vector(384))",
                (unit(384),),
            )
            await conn.execute(
                "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding_1024) "
                "VALUES ('multi', 'BAAI/bge-m3', 'u1', %s::halfvec(1024))",
                (unit(1024),),
            )
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M23 - 1)), prepare=False
        )
        # the stated data loss: the multilingual vector is gone, and nothing else is
        assert await rows(conn, "SELECT version_id FROM memory_embedding ORDER BY version_id") == [("legacy",)]
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(2,)]
