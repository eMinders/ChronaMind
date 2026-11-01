"""E-020 / M8: the ``um_facet`` tag and the retention floors, enforced by PostgreSQL (V2-D26, V2-D29, V2-D10)."""

from pathlib import Path
from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation

from custom_memory_engine.model.axes import FACETS, KINDS, RETENTIONS, SHORT_RETENTION_KINDS
from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import adapt, rollback_sql
from tests.integration.test_migration_m1 import (
    START,
    assert_axes_consistent,
    connect,
    database,  # noqa: F401  (fixture)
    insert_active,
    rows,
    seed_turn,
)

pytestmark = pytest.mark.integration
M8 = 6
USER_MODEL = Path(__file__).resolve().parents[2] / "PLAN" / "V2" / "USER_MODEL.md"
KINDS_WITH_FLOOR = ("claim", "abstraction")
LAYER_OF = {"claim": "semantic", "abstraction": "persona", "episode": "episodic", "procedure": "semantic"}


async def insert_row(
    conn: AsyncConnection, version_id: str, kind: str, *, retention: str = "durable", facet: str | None = None
) -> None:
    await seed_turn(conn, f"t-{version_id}", "I am vegetarian", START)
    async with conn.transaction():
        await conn.execute(
            await adapt(
                conn,
                "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                "single_valued, content, event_range, recorded_range, layer, kind, retention, um_facet) "
                "VALUES (%s, %s, 'u1', 'active', 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
                "tstzrange(clock_now(), NULL), %s, %s, %s, %s)",
            ),
            (version_id, version_id, START, LAYER_OF[kind], kind, retention, facet),
        )
        await conn.execute(
            "INSERT INTO memory_evidence VALUES (%s, %s, 'u1', 'I am vegetarian', 'support')",
            (version_id, f"t-{version_id}"),
        )


# ----------------------------------------------------------------------------------------- um_facet


@pytest.mark.parametrize("facet", FACETS)
async def test_each_of_the_six_canonical_facets_is_accepted(database, facet: str) -> None:  # noqa: F811
    async with connect(database) as conn:
        await insert_row(conn, "v1", "claim", facet=facet)
        assert await rows(conn, "SELECT um_facet FROM memory_version WHERE id = 'v1'") == [(facet,)]


@pytest.mark.parametrize("facet", ["inferred_preference", "preferences", "Preference", "", "seventh"])
async def test_inferred_preference_and_any_other_value_is_rejected(database, facet: str) -> None:  # noqa: F811
    """``inferred_preference`` was an r1/r2 facet; inference is a lifecycle, not a facet."""
    async with connect(database) as conn:
        with pytest.raises(CheckViolation, match="um_facet_check"):
            await insert_row(conn, "v1", "claim", facet=facet)
        await insert_row(conn, "v2", "claim", facet="goal")
        with pytest.raises(CheckViolation, match="um_facet_check"):
            await conn.execute("UPDATE memory_version SET um_facet = %s WHERE id = 'v2'", (facet,))


async def test_no_facet_is_allowed_and_the_tag_can_be_cleared(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await insert_row(conn, "v1", "episode")
        assert await rows(conn, "SELECT um_facet FROM memory_version WHERE id = 'v1'") == [(None,)]
        await conn.execute("UPDATE memory_version SET um_facet = 'goal' WHERE id = 'v1'")
        await conn.execute("UPDATE memory_version SET um_facet = NULL WHERE id = 'v1'")


def test_the_six_facets_are_the_ones_the_user_model_document_defines() -> None:
    line = next(
        line for line in USER_MODEL.read_text(encoding="utf-8").splitlines() if line.startswith("preference | goal")
    )
    assert tuple(part.strip() for part in line.split("|")) == FACETS


async def test_the_sql_facet_set_is_exactly_the_model_facet_set(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        (definition,) = (
            await rows(
                conn,
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'memory_version_um_facet_check' AND conrelid = 'memory_version'::regclass",
            )
        )[0]
    quoted = {part.split("'")[1] for part in str(definition).split("::text") if "'" in part}
    assert quoted == set(FACETS) and len(FACETS) == 6


# ----------------------------------------------------------------------------------------- the floors


@pytest.mark.parametrize("retention", ["working", "session"])
@pytest.mark.parametrize("kind", KINDS_WITH_FLOOR)
async def test_claims_and_abstractions_cannot_be_working_or_session_on_insert(
    database,  # noqa: F811
    kind: str,
    retention: str,
) -> None:
    async with connect(database) as conn:
        with pytest.raises(CheckViolation, match="retention_floor"):
            await insert_row(conn, "v1", kind, retention=retention)
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(0,)]


@pytest.mark.parametrize("retention", ["working", "session"])
@pytest.mark.parametrize("kind", KINDS_WITH_FLOOR)
async def test_claims_and_abstractions_cannot_be_moved_to_working_or_session_on_update(
    database,  # noqa: F811
    kind: str,
    retention: str,
) -> None:
    async with connect(database) as conn:
        await insert_row(conn, "v1", kind)
        with pytest.raises(CheckViolation, match="retention_floor"):
            await conn.execute("UPDATE memory_version SET retention = %s WHERE id = 'v1'", (retention,))
        assert await rows(conn, "SELECT retention FROM memory_version WHERE id = 'v1'") == [("durable",)]


@pytest.mark.parametrize("retention", RETENTIONS)
@pytest.mark.parametrize("kind", sorted(SHORT_RETENTION_KINDS))
async def test_episodes_and_procedures_accept_every_retention(database, kind: str, retention: str) -> None:  # noqa: F811
    async with connect(database) as conn:
        await insert_row(conn, "v1", kind, retention=retention)
        for other in RETENTIONS:  # and they may move between all three
            await conn.execute("UPDATE memory_version SET retention = %s WHERE id = 'v1'", (other,))
        assert await rows(conn, "SELECT kind, retention FROM memory_version WHERE id = 'v1'") == [
            (kind, RETENTIONS[-1])
        ]


async def test_the_floor_in_sql_is_exactly_the_model_floor(database) -> None:  # noqa: F811
    """Every ``(kind, retention)`` pair is accepted by the database iff ``validate_axes`` accepts its retention."""
    async with connect(database) as conn:
        for kind in KINDS:
            for retention in RETENTIONS:
                allowed = retention == "durable" or kind in SHORT_RETENTION_KINDS
                version_id = f"{kind}-{retention}"
                if allowed:
                    await insert_row(conn, version_id, kind, retention=retention)
                else:
                    with pytest.raises(CheckViolation, match="retention_floor"):
                        await insert_row(conn, version_id, kind, retention=retention)
        accepted = await rows(conn, "SELECT kind, retention FROM memory_version ORDER BY 1, 2")
        assert len(accepted) == len(KINDS) * len(RETENTIONS) - len(KINDS_WITH_FLOOR) * 2


async def test_v1_write_paths_still_produce_durable_rows(database) -> None:  # noqa: F811
    """v1 has no working/session value, so the floor never rejects a v1 writer."""
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await insert_active(conn, "v1", layer="persona")
        await insert_active(conn, "v2", layer="episodic")
        found = await assert_axes_consistent(conn)
        assert {value[2] for value in found.values()} == {"durable"}


# ----------------------------------------------------------------------------------------- backfill and index


async def test_backfill_leaves_every_existing_row_without_a_facet(database) -> None:  # noqa: F811
    config, schema = database
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M8 - 1)), prepare=False
        )
        await seed_turn(conn, "t1", "I am vegetarian", START)
        for version_id, layer in (("a", "episodic"), ("b", "semantic"), ("c", "opinion"), ("d", "persona")):
            await insert_active(conn, version_id, layer=layer)
    await migrate.MigrationRunner(
        migrate.MigrationConfig(database_url=config.database_url, schema_name=schema)
    ).apply()
    async with connect(database) as conn:
        assert await rows(conn, "SELECT count(*), count(um_facet) FROM memory_version") == [(4, 0)]


async def test_the_facet_index_supports_facet_filtered_queries_per_user(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        definition = (
            await rows(conn, "SELECT indexdef FROM pg_indexes WHERE indexname = 'memory_version_um_facet_idx'")
        )[0][0]
        assert "(user_id, um_facet)" in str(definition) and "WHERE (um_facet IS NOT NULL)" in str(definition)
        await insert_row(conn, "v1", "claim", facet="goal")
        await insert_row(conn, "v2", "claim")
        await conn.execute("SET enable_seqscan = off")
        plan = " ".join(
            str(row[0])
            for row in await rows(
                conn, "EXPLAIN SELECT id FROM memory_version WHERE user_id = 'u1' AND um_facet = 'goal'"
            )
        )
        assert "memory_version_um_facet_idx" in plan
        assert await rows(conn, "SELECT id FROM memory_version WHERE user_id = 'u1' AND um_facet = 'goal'") == [
            ("v1",)
        ]


# ----------------------------------------------------------------------------------------- rollback


async def test_rollback_restores_version_5_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await insert_row(conn, "keep", "claim", facet="goal")
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M8 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M8 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]
        columns = await rows(
            conn,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'memory_version' AND column_name = 'um_facet'",
        )
        assert columns == []
        # the floor is gone with it: a working claim is representable again (which is exactly why M8 must precede W2)
        await insert_active(conn, "loose", extra="retention='working'")
        await conn.execute("DELETE FROM memory_version WHERE id = 'loose'")
    status = await runner.status()
    assert (status.state, status.version) == ("pending", M8 - 1)
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await rows(conn, "SELECT count(*), count(um_facet) FROM memory_version") == [(1, 0)]  # tag was dropped
        with pytest.raises(CheckViolation, match="retention_floor"):
            await insert_row(conn, "again", "claim", retention="session")
