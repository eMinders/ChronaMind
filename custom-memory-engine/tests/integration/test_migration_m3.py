"""E-012 / M3: the three clocks - event_range renamed in place, observed_at, event_basis, and the T-28 watermark."""

from datetime import UTC, datetime
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, DivisionByZero, ExclusionViolation, InsufficientPrivilege, RaiseException

from custom_memory_engine.schema import migrate
from custom_memory_engine.security import privilege_violations, provision_runtime_role
from tests.integration.rollbacks import adapt, rollback_sql
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
    seed_turn,
)

pytestmark = pytest.mark.integration
M3 = 9
SRC = Path(migrate.__file__).resolve().parents[1]
JUNE = datetime(2026, 6, 1, tzinfo=UTC)
JULY = datetime(2026, 7, 1, tzinfo=UTC)
AUGUST = datetime(2026, 8, 1, tzinfo=UTC)


async def seed_user_graph(conn: AsyncConnection) -> None:
    await seed_turn(conn, "t1", "I am vegetarian", START)


async def set_clock(conn: AsyncConnection, at: datetime) -> None:
    await conn.execute("SELECT set_config('app.clock', %s, false)", (at.isoformat(),))


async def believe(
    conn: AsyncConnection, version_id: str, *, user: str = "u1", single: bool = True, key: str = "diet"
) -> None:
    """One believed version, recorded at the session clock, with evidence. Works on either side of migration 9."""
    async with conn.transaction():
        await conn.execute(
            await adapt(
                conn,
                "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                "single_valued, content, event_range, recorded_range, layer) VALUES (%s, %s, %s, 'active', 'user', "
                "'diet', %s, 'x', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic')",
            ),
            (version_id, key, user, single, START),
        )
        await conn.execute(
            "INSERT INTO memory_evidence VALUES (%s, 't1', %s, 'I am vegetarian', 'support')", (version_id, user)
        )


# ----------------------------------------------------------------------------------- atomicity and equivalence


async def at_version_8(database) -> migrate.MigrationRunner:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M3 - 1)), prepare=False
        )
    return runner


async def test_a_failure_inside_the_migration_leaves_the_previous_catalog_and_constraint(database) -> None:  # noqa: F811
    """Acceptance 1: inject a failure after the rename; the schema is exactly as it was, constraint included."""
    runner = await at_version_8(database)
    source = (SRC / "schema" / "009_clocks.sql").read_text(encoding="utf-8")
    async with connect(database) as conn:
        before_catalog = await runner._catalog(conn)
        before_constraint = await rows(
            conn,
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'no_bitemporal_overlap' AND conrelid = 'memory_version'::regclass",
        )
        with pytest.raises(DivisionByZero):
            async with conn.transaction():
                await conn.execute(cast(LiteralString, source), prepare=False)
                assert await rows(conn, "SELECT count(*) FROM clock_watermark") == [(0,)]  # it did run...
                await conn.execute("SELECT 1 / 0")  # ...then fails after the constraint was carried over
        assert await runner._catalog(conn) == before_catalog
        assert (
            await rows(
                conn,
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'no_bitemporal_overlap' AND conrelid = 'memory_version'::regclass",
            )
            == before_constraint
        )
        assert "valid_range" in str(before_constraint[0][0])
        assert await rows(conn, "SELECT to_regclass('clock_watermark')") == [(None,)]


async def overlapping_pair_is_refused(conn: AsyncConnection) -> bool:
    await believe(conn, "a", single=True)
    try:
        await set_clock(conn, JULY)
        async with conn.transaction():
            await conn.execute(
                await adapt(
                    conn,
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, recorded_range, layer) VALUES ('b', 'diet-2', 'u1', "
                    "'active', 'user', 'diet', true, 'y', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), "
                    "'semantic')",
                ),
                (START,),
            )
            await conn.execute("INSERT INTO memory_evidence VALUES ('b', 't1', 'u1', 'I am vegetarian', 'support')")
    except ExclusionViolation as error:
        return error.sqlstate == "23P01"
    return False


async def test_the_exclusion_constraint_is_carried_over_unchanged_in_catalog_and_behaviour(database) -> None:  # noqa: F811
    """Acceptance 3: only the column name differs, and 23P01 fires before and after."""
    runner = await at_version_8(database)
    async with connect(database) as conn:
        await seed_user_graph(conn)
        await set_clock(conn, JUNE)
        assert await overlapping_pair_is_refused(conn)  # before (valid_range)
        before = await rows(
            conn,
            "SELECT pg_get_constraintdef(oid), conname FROM pg_constraint "
            "WHERE conrelid = 'memory_version'::regclass AND conname IN ('no_bitemporal_overlap', 'valid_wellformed')",
        )
        index_before = await rows(
            conn,
            "SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indexrelid = "
            "'memory_version_temporal_idx'::regclass",
        )
        async with conn.transaction():
            await conn.execute("DELETE FROM memory_version")
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        after = await rows(
            conn,
            "SELECT pg_get_constraintdef(oid), conname FROM pg_constraint "
            "WHERE conrelid = 'memory_version'::regclass AND conname IN ('no_bitemporal_overlap', 'valid_wellformed')",
        )
        assert [(str(d).replace("valid_range", "event_range"), n) for d, n in before] == list(after)
        index_after = await rows(
            conn,
            "SELECT pg_get_indexdef(indexrelid) FROM pg_index "
            "WHERE indexrelid = 'memory_version_temporal_idx'::regclass",
        )
        assert str(index_before[0][0]).replace("valid_range", "event_range") == index_after[0][0]
        await conn.execute("DELETE FROM clock_watermark")  # the scenario above ran at an earlier instant
        await set_clock(conn, JUNE)
        assert await overlapping_pair_is_refused(conn)  # after (event_range)


# ----------------------------------------------------------------------------------- observed_at and event_basis


async def test_observed_at_backfill_follows_decision_d8(database) -> None:  # noqa: F811
    """Believed -> belief start; unbelieved but evidenced -> earliest evidencing turn; neither -> event start."""
    runner = await at_version_8(database)
    async with connect(database) as conn:
        await seed_user_graph(conn)  # turn t1 occurred at START
        await set_clock(conn, JUNE)
        await believe(conn, "believed", single=False, key="k1")
        async with conn.transaction():
            await conn.execute(
                await adapt(
                    conn,
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, layer) VALUES ('held', 'k2', 'u1', 'conflict_pending', "
                    "'user', 'diet', false, 'x', tstzrange(%s, NULL), 'semantic')",
                ),
                (AUGUST,),
            )
            await conn.execute("INSERT INTO memory_evidence VALUES ('held', 't1', 'u1', 'I am vegetarian', 'support')")
            await conn.execute(
                await adapt(
                    conn,
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, layer) VALUES ('bare', 'k3', 'u1', 'rejected', "
                    "'user', 'diet', false, 'x', tstzrange(%s, NULL), 'semantic')",
                ),
                (JULY,),
            )
        async with conn.transaction():
            await conn.execute(
                "UPDATE memory_version SET valid_from_basis = 'explicit' WHERE false"
            )  # keep the old column name in play
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        observed = {str(r[0]): r[1] for r in await rows(conn, "SELECT id, observed_at FROM memory_version")}
        assert observed["believed"] == JUNE  # lower(recorded_range)
        assert observed["held"] == START  # the turn that evidences it, not its (later) event start
        assert observed["bare"] == JULY  # lower(event_range)
        assert await rows(
            conn, "SELECT count(*) FROM memory_version WHERE observed_at IS NULL OR event_basis IS NULL"
        ) == [(0,)]


async def test_new_rows_get_the_ingest_clock_and_inherit_the_basis_and_both_are_fixed(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_user_graph(conn)
        await set_clock(conn, JUNE)
        await believe(conn, "v1", single=False)
        assert await rows(conn, "SELECT observed_at, event_basis, valid_from_basis FROM memory_version") == [
            (JUNE, "inferred", "inferred")
        ]
        async with conn.transaction():  # a v1 writer that sets the old column
            await conn.execute(
                "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                "single_valued, content, event_range, recorded_range, layer, valid_from_basis) VALUES ('v2', 'k2', "
                "'u1', 'active', 'user', 'diet', false, 'x', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), "
                "'semantic', 'explicit')",
                (START,),
            )
            await conn.execute("INSERT INTO memory_evidence VALUES ('v2', 't1', 'u1', 'I am vegetarian', 'support')")
        async with conn.transaction():  # new code that sets event_basis and an explicit observed_at
            await conn.execute(
                "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                "single_valued, content, event_range, recorded_range, layer, event_basis, observed_at) VALUES "
                "('v3', 'k3', 'u1', 'active', 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
                "tstzrange(clock_now(), NULL), 'semantic', 'explicit', %s)",
                (START, START),
            )
            await conn.execute("INSERT INTO memory_evidence VALUES ('v3', 't1', 'u1', 'I am vegetarian', 'support')")
        assert await rows(
            conn, "SELECT id, event_basis, valid_from_basis, observed_at FROM memory_version ORDER BY id"
        ) == [
            ("v1", "inferred", "inferred", JUNE),
            ("v2", "explicit", "explicit", JUNE),
            ("v3", "explicit", "explicit", START),
        ]
        with pytest.raises(RaiseException, match="fixed at creation"):
            await conn.execute("UPDATE memory_version SET observed_at = now() WHERE id = 'v1'")
        with pytest.raises(RaiseException, match="fixed at creation"):
            await conn.execute("UPDATE memory_version SET event_basis = 'explicit' WHERE id = 'v1'")
        with pytest.raises(CheckViolation, match="event_basis"):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, layer, event_basis) VALUES ('bad', 'k9', 'u1', "
                    "'rejected', 'user', 'diet', false, 'x', tstzrange(%s, NULL), 'semantic', 'guess')",
                    (START,),
                )


# ----------------------------------------------------------------------------------- T-28


async def test_recorded_time_cannot_move_backwards_whatever_app_clock_says(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_user_graph(conn)
        await set_clock(conn, JULY)
        await believe(conn, "now", single=False, key="k1")
        await set_clock(conn, JUNE)  # a compromised session winds the clock back
        with pytest.raises(RaiseException, match="recorded_time_moved_backwards"):
            await believe(conn, "backdated", single=False, key="k2")
        assert await rows(conn, "SELECT id FROM memory_version ORDER BY id") == [("now",)]
        await set_clock(conn, JULY)  # equal is fine (a supersession closes and opens at one instant)
        await believe(conn, "same-instant", single=False, key="k3")
        await set_clock(conn, AUGUST)
        await believe(conn, "later", single=False, key="k4")
        assert await rows(conn, "SELECT high FROM clock_watermark") == [(AUGUST,)]


async def test_closing_a_belief_in_the_past_is_refused_too(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_user_graph(conn)
        await set_clock(conn, JUNE)
        await believe(conn, "old", single=False)
        await set_clock(conn, AUGUST)
        await believe(conn, "new", single=False, key="k2")
        await set_clock(conn, JULY)
        with pytest.raises(RaiseException, match="recorded_time_moved_backwards"):
            await conn.execute(
                "UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) "
                "WHERE id = 'old'"
            )


async def test_the_watermark_is_per_user(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s2', 'u2', %s)", (START,))
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES ('t2', 's2', 'u2', 1, 'user', 'x', %s)",
            (START,),
        )
        await seed_user_graph(conn)
        await set_clock(conn, AUGUST)
        await believe(conn, "u1-late", single=False)
        await set_clock(conn, JUNE)  # u2 has never been recorded later than June, so June is fine for u2
        async with conn.transaction():
            await conn.execute(
                await adapt(
                    conn,
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, recorded_range, layer) VALUES ('u2-v', 'k', 'u2', 'active', "
                    "'user', 'diet', false, 'x', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic')",
                ),
                (START,),
            )
            await conn.execute("INSERT INTO memory_evidence VALUES ('u2-v', 't2', 'u2', 'x', 'support')")
        assert {str(r[0]): r[1] for r in await rows(conn, "SELECT user_id, high FROM clock_watermark")} == {
            "u1": AUGUST,
            "u2": JUNE,
        }


async def test_the_backfill_seeds_the_watermark_from_existing_rows(database) -> None:  # noqa: F811
    runner = await at_version_8(database)
    async with connect(database) as conn:
        await seed_user_graph(conn)
        await set_clock(conn, JULY)
        await believe(conn, "v1", single=False)
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await rows(conn, "SELECT user_id, high FROM clock_watermark") == [("u1", JULY)]
        await set_clock(conn, JUNE)
        with pytest.raises(RaiseException, match="recorded_time_moved_backwards"):
            await believe(conn, "late-backdate", single=False, key="k2")


async def test_the_runtime_role_can_neither_read_nor_forge_the_watermark(database) -> None:  # noqa: F811
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    try:
        async with connect(database) as admin:
            await provision_runtime_role(
                admin, schema, role
            )  # verifies the exact privilege matrix, new table included
            assert await privilege_violations(admin, schema, role) == []
            await seed_user_graph(admin)
        async with connect(database) as app:
            await app.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            with pytest.raises(InsufficientPrivilege):
                await app.execute("SELECT * FROM clock_watermark")
            with pytest.raises(InsufficientPrivilege):
                await app.execute("INSERT INTO clock_watermark VALUES ('u1', '2000-01-01')")
            with pytest.raises(InsufficientPrivilege):
                await app.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_recorded_watermark")
            # yet its own writes advance the watermark (the trigger runs as the owner)
            await believe(app, "mine", single=False)
        async with connect(database) as admin:
            assert await rows(admin, "SELECT user_id FROM clock_watermark") == [("u1",)]
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


# ----------------------------------------------------------------------------------- rollback and the code rename


async def test_rollback_restores_version_8_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await seed_user_graph(conn)
        await set_clock(conn, JUNE)
        await believe(conn, "keep", single=False)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M3 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M3 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical, functions and constraint included
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE true") == [(1,)]
        columns = {
            row[0]
            for row in await rows(
                conn,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'memory_version'",
            )
        }
        assert "valid_range" in columns and not {"event_range", "observed_at", "event_basis"} & columns
        body = (await rows(conn, "SELECT pg_get_functiondef('guard_update'::regproc)"))[0][0]
        assert "valid_range" in str(body) and "event_range" not in str(body)
    status = await runner.status()
    assert (status.state, status.version) == ("pending", M3 - 1)
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await rows(conn, "SELECT id, observed_at FROM memory_version") == [("keep", JUNE)]
        body = (await rows(conn, "SELECT pg_get_functiondef('guard_update'::regproc)"))[0][0]
        assert "event_range" in str(body) and "valid_range" not in str(body)


def test_no_engine_code_refers_to_valid_range_any_more() -> None:
    """Acceptance 5: only the historical, checksum-bound migrations (and their rollbacks) keep the old name."""
    historical = {"001_full_schema.sql", "005_axes.sql"}
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".sql"}
        and "valid_range" in path.read_text(encoding="utf-8")
        and path.name not in historical
        and path.parent.name != "rollback"
        and path.name != "009_clocks.sql"
    ]
    assert offenders == []
