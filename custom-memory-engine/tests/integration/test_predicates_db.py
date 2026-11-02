"""RL-043 temporal selection against evidence-backed full-schema PostgreSQL rows."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, pq, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.engine.predicates import (
    TemporalQuery,
    audit_as_of,
    compile_predicate,
    current_state,
    fetch_versions,
    historical_as_of,
    historical_window,
)
from tests.integration.schema import apply_migrations

pytestmark = pytest.mark.integration

CME_ROOT = Path(__file__).resolve().parents[2]
MICROSECOND = timedelta(microseconds=1)


def instant(month: int, day: int) -> datetime:
    return datetime(2026, month, day, tzinfo=UTC)


MAR10 = instant(3, 10)
APR1 = instant(4, 1)
JUN1 = instant(6, 1)
JUL15 = instant(7, 15)
JUL20 = instant(7, 20)
AUG5 = instant(8, 5)
AUG10 = instant(8, 10)
AUG20 = instant(8, 20)
OCT1 = instant(10, 1)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable predicate test database")
    config = DatabaseConfig.from_environment()
    schema = "cme_predicates_" + uuid4().hex
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        async with connect((config, schema)) as conn:
            await apply_migrations(conn)
            await seed_history(conn)
        yield config, schema
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET statement_timeout = '30s'")
        await conn.execute("SET timezone = 'UTC'")
        yield conn


async def identity(conn: AsyncConnection, user: str, clock: datetime = OCT1, *, local: bool = False) -> None:
    await conn.execute("SELECT set_config('app.user_id', %s, %s)", (user, local))
    await conn.execute("SELECT set_config('app.clock', %s, %s)", (clock.isoformat(), local))


async def insert_version(
    conn: AsyncConnection,
    version_id: str,
    valid_from: datetime,
    valid_until: datetime | None,
    *,
    user: str = "u1",
    subject: str = "user",
    status: str = "active",
    content: str = "vegetarian",
) -> None:
    await conn.execute(
        """
        INSERT INTO memory_version (
            id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content,
            event_range, recorded_range
        ) VALUES (
            %s, 'diet', %s, %s, %s, 'diet', true, %s,
            tstzrange(%s, %s, '[)'),
            CASE WHEN %s IN ('active', 'archived') THEN tstzrange(clock_now(), NULL, '[)') ELSE NULL END
        )
        """,
        (version_id, user, status, subject, content, valid_from, valid_until, status),
    )
    await conn.execute(
        """INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role)
           VALUES (%s, %s, %s, 'I am vegetarian and later eat fish', 'support')""",
        (version_id, "turn-" + user, user),
    )


async def seed_history(conn: AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO predicate_schema (predicate_key, cardinality, default_layer) VALUES ('diet','single','semantic')"
    )
    for user in ("u1", "u2"):
        await identity(conn, user, MAR10)
        await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "external-" + user, MAR10))
        await conn.execute(
            "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", ("session-" + user, user, MAR10)
        )
        await conn.execute(
            """INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at)
               VALUES (%s, %s, %s, 1, 'user', 'I am vegetarian and later eat fish', %s)""",
            ("turn-" + user, "session-" + user, user, MAR10),
        )
    await identity(conn, "u1", MAR10)
    async with conn.transaction():
        await insert_version(conn, "v1", MAR10, None)
    await identity(conn, "u1", AUG5)
    async with conn.transaction():
        await conn.execute(
            "UPDATE memory_version SET recorded_range=tstzrange(lower(recorded_range), clock_now()) WHERE id='v1'"
        )
        await insert_version(conn, "v1p", MAR10, AUG5)
        await insert_version(conn, "v2", AUG5, None, content="fish")
    await identity(conn, "u1", AUG20)
    async with conn.transaction():
        await conn.execute(
            "UPDATE memory_version SET recorded_range=tstzrange(lower(recorded_range), clock_now()) "
            "WHERE id IN ('v1p','v2')"
        )
        await insert_version(conn, "v1pp", MAR10, JUL15)
        await insert_version(conn, "v2p", JUL15, None, content="fish")
        for status in ("candidate", "conflict_pending", "rejected"):
            await insert_version(conn, status, MAR10, None, status=status)
    await identity(conn, "u2", MAR10)
    async with conn.transaction():
        await insert_version(conn, "other-user", MAR10, None, user="u2", content="other user's private diet")


async def selected(conn: AsyncConnection, schema: str, query: TemporalQuery) -> set[str]:
    result = await fetch_versions(conn, query, schema_name=schema)
    return {version.id for version in result.versions}


def ordinary_and_audit(user: str) -> tuple[TemporalQuery, ...]:
    return (
        current_state(user, OCT1),
        historical_as_of(user, APR1, OCT1),
        historical_window(user, instant(3, 1), OCT1, OCT1),
        audit_as_of(user, APR1, JUN1),
    )


async def test_worked_example_returns_corrected_history_and_original_audit_beliefs(database) -> None:
    _, schema = database
    cases = (
        (current_state("u1", OCT1), {"v2p"}),
        (historical_as_of("u1", APR1, OCT1), {"v1pp"}),
        (historical_as_of("u1", JUL20, OCT1), {"v2p"}),
        (historical_window("u1", instant(3, 1), OCT1, OCT1), {"v1pp", "v2p"}),
        (audit_as_of("u1", APR1, JUN1), {"v1"}),
        (audit_as_of("u1", JUL20, AUG10), {"v1p"}),
        (audit_as_of("u1", AUG10, AUG10), {"v2"}),
        (audit_as_of("u1", instant(9, 1), JUN1), {"v1"}),
    )
    async with connect(database) as conn:
        await identity(conn, "u1")
        for query, expected in cases:
            assert await selected(conn, schema, query) == expected
        result = await fetch_versions(conn, current_state("u1", OCT1), schema_name=schema)
        version = result.versions[0]
        assert version.content == "fish"
        assert version.user_id == "u1"
        assert version.event_range.lower == JUL15 and version.event_range.upper is None
        assert version.recorded_range is not None
        assert version.recorded_range.lower == AUG20 and version.recorded_range.upper is None


async def test_recorded_containment_uses_reference_instead_of_open_upper_bound(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        assert await selected(conn, schema, current_state("u1", JUN1)) == {"v1"}
        assert await selected(conn, schema, historical_as_of("u1", APR1, AUG10)) == {"v1p"}
        assert await selected(conn, schema, historical_window("u1", MAR10, AUG10, AUG10)) == {"v1p", "v2"}
        assert await selected(conn, schema, current_state("u1", MAR10 - MICROSECOND)) == set()
        await identity(conn, "u1", instant(11, 1))
        async with conn.transaction():
            await insert_version(conn, "future-recorded", MAR10, None, subject="future-recorded")
        await identity(conn, "u1")
        assert await selected(conn, schema, current_state("u1", OCT1)) == {"v2p"}


async def test_valid_and_recorded_half_open_boundaries(database) -> None:
    _, schema = database
    cases = (
        (historical_as_of("u1", MAR10 - MICROSECOND, OCT1), set()),
        (historical_as_of("u1", MAR10, OCT1), {"v1pp"}),
        (historical_as_of("u1", JUL15 - MICROSECOND, OCT1), {"v1pp"}),
        (historical_as_of("u1", JUL15, OCT1), {"v2p"}),
        (historical_as_of("u1", OCT1, OCT1), {"v2p"}),
        (audit_as_of("u1", APR1, AUG5 - MICROSECOND), {"v1"}),
        (audit_as_of("u1", APR1, AUG5), {"v1p"}),
        (audit_as_of("u1", JUL20, AUG20 - MICROSECOND), {"v1p"}),
        (audit_as_of("u1", JUL20, AUG20), {"v2p"}),
    )
    async with connect(database) as conn:
        await identity(conn, "u1")
        for query, expected in cases:
            assert await selected(conn, schema, query) == expected


async def test_change_window_distinguishes_overlap_from_touching_endpoints(database) -> None:
    _, schema = database
    cases = (
        (instant(3, 1), MAR10, set()),
        (MAR10, JUL15, {"v1pp"}),
        (JUL15, AUG5, {"v2p"}),
        (JUL15 - MICROSECOND, JUL15 + MICROSECOND, {"v1pp", "v2p"}),
    )
    async with connect(database) as conn:
        await identity(conn, "u1")
        for start, end, expected in cases:
            assert await selected(conn, schema, historical_window("u1", start, end, OCT1)) == expected


async def test_explicit_timestamp_infinity_round_trips_distinct_from_unbounded_ends(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1", AUG20)
        async with conn.transaction():
            await insert_version(conn, "unbounded-both", MAR10, None, subject="unbounded")
            await conn.execute(
                """INSERT INTO memory_version (
                       id, memory_key, user_id, status, subject_key, predicate_key, single_valued,
                       content, event_range, recorded_range
                   ) VALUES (
                       'literal-infinity', 'diet', 'u1', 'active', 'infinity', 'diet', true,
                       'infinite upper bounds', tstzrange(%s, 'infinity'::timestamptz, '[)'),
                       tstzrange(clock_now(), 'infinity'::timestamptz, '[)')
                   )""",
                (MAR10,),
            )
            await conn.execute(
                """INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role)
                   VALUES ('literal-infinity', 'turn-u1', 'u1', 'I am vegetarian', 'support')"""
            )
        await identity(conn, "u1")
        await conn.execute("SET DateStyle TO 'SQL, DMY'")
        await conn.execute("SET timezone = 'Africa/Cairo'")
        result = await fetch_versions(conn, current_state("u1", OCT1), schema_name=schema)
        by_id = {version.id: version for version in result.versions}
        assert set(by_id) == {"v2p", "unbounded-both", "literal-infinity"}
        unbounded = by_id["unbounded-both"]
        literal = by_id["literal-infinity"]
        assert unbounded.event_range.upper is None
        assert unbounded.recorded_range is not None and unbounded.recorded_range.upper is None
        assert literal.event_range.upper == "infinity"
        assert literal.recorded_range is not None and literal.recorded_range.upper == "infinity"
        assert literal.event_range.lower == MAR10 and literal.recorded_range.lower == AUG20
        assert literal.event_range.lower_inclusive and not literal.event_range.upper_inclusive
        cursor = await conn.execute(
            """SELECT id, upper_inf(event_range), upper_inf(recorded_range)
               FROM memory_version WHERE id IN ('unbounded-both','literal-infinity') ORDER BY id"""
        )
        assert await cursor.fetchall() == [("literal-infinity", False, False), ("unbounded-both", True, True)]


async def test_archived_rows_are_visible_only_to_audit_even_at_matching_recorded_time(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        await conn.execute("UPDATE memory_version SET status='archived', archival_branch='S' WHERE id='v1'")
        assert await selected(conn, schema, current_state("u1", JUN1)) == set()
        assert await selected(conn, schema, historical_as_of("u1", APR1, JUN1)) == set()
        assert await selected(conn, schema, historical_window("u1", MAR10, JUN1, JUN1)) == set()
        assert await selected(conn, schema, audit_as_of("u1", APR1, JUN1)) == {"v1"}
        assert await selected(conn, schema, historical_as_of("u1", APR1, OCT1)) == {"v1pp"}


async def test_future_guards_use_injected_database_clock_including_empty_results(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        assert await selected(conn, schema, audit_as_of("u1", APR1, OCT1)) == {"v1pp"}
        for user in ("u1", "no-such-user"):
            await identity(conn, user)
            with pytest.raises(InvalidParameterValue) as failure:
                await fetch_versions(conn, audit_as_of(user, APR1, OCT1 + MICROSECOND), schema_name=schema)
            assert failure.value.sqlstate == "22023"
            cursor = await conn.execute("SELECT 1")
            assert await cursor.fetchone() == (1,)
        await identity(conn, "u1", JUN1)
        with pytest.raises(InvalidParameterValue):
            await fetch_versions(conn, audit_as_of("u1", APR1, JUN1 + MICROSECOND), schema_name=schema)
        assert await selected(conn, schema, audit_as_of("u1", APR1, JUN1)) == {"v1"}


async def test_audit_without_override_uses_the_same_transaction_clock(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.clock', '', true)")
            cursor = await conn.execute("SELECT transaction_timestamp()")
            row = await cursor.fetchone()
            assert row is not None
            clock = row[0]
            assert await selected(conn, schema, audit_as_of("u1", APR1, clock)) == {"v1pp"}
            with pytest.raises(InvalidParameterValue):
                await fetch_versions(conn, audit_as_of("u1", APR1, clock + MICROSECOND), schema_name=schema)
            assert conn.info.transaction_status == pq.TransactionStatus.INTRANS


async def test_executor_refuses_missing_and_cross_user_identity_without_rewriting_it(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        for user in ("", "u2"):
            await identity(conn, user)
            for query in ordinary_and_audit("u1"):
                with pytest.raises(InsufficientPrivilege):
                    await fetch_versions(conn, query, schema_name=schema)
                cursor = await conn.execute("SELECT current_setting('app.user_id', true)")
                assert await cursor.fetchone() == (user,)


async def test_executor_resolves_table_and_replay_clock_in_requested_schema(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        await conn.execute("SET search_path TO pg_catalog, public")
        expected = ({"v2p"}, {"v1pp"}, {"v1pp", "v2p"}, {"v1"})
        for query, wanted in zip(ordinary_and_audit("u1"), expected, strict=True):
            assert await selected(conn, schema, query) == wanted
        with pytest.raises(InvalidParameterValue):
            await fetch_versions(conn, audit_as_of("u1", APR1, OCT1 + MICROSECOND), schema_name=schema)


async def test_fetch_preserves_outer_transaction_and_local_gucs_on_success_and_rollback(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await conn.execute("SELECT set_config('app.user_id', '', false)")
        await conn.execute("SELECT set_config('app.clock', '', false)")
        with pytest.raises(RuntimeError, match="rollback-owner"):
            async with conn.transaction():
                await identity(conn, "u1", local=True)
                await conn.execute("INSERT INTO app_user VALUES ('rollback-probe', 'rollback-probe', %s)", (OCT1,))
                assert await selected(conn, schema, current_state("u1", OCT1)) == {"v2p"}
                assert conn.info.transaction_status == pq.TransactionStatus.INTRANS
                cursor = await conn.execute(
                    "SELECT current_setting('app.user_id'), current_setting('app.clock')::timestamptz"
                )
                assert await cursor.fetchone() == ("u1", OCT1)
                raise RuntimeError("rollback-owner")
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
        cursor = await conn.execute(
            "SELECT nullif(current_setting('app.user_id', true), ''), nullif(current_setting('app.clock', true), '')"
        )
        assert await cursor.fetchone() == (None, None)
        await identity(conn, "rollback-probe")
        cursor = await conn.execute("SELECT count(*) FROM app_user WHERE id='rollback-probe'")
        assert await cursor.fetchone() == (0,)
        await conn.execute("SELECT set_config('app.user_id', '', false)")
        await conn.execute("SELECT set_config('app.clock', %s, false)", (JUN1.isoformat(),))
        async with conn.transaction():
            await identity(conn, "u1", local=True)
            assert await selected(conn, schema, current_state("u1", OCT1)) == {"v2p"}
        cursor = await conn.execute(
            "SELECT nullif(current_setting('app.user_id', true), ''), "
            "nullif(current_setting('app.clock', true), '')::timestamptz"
        )
        assert await cursor.fetchone() == (None, JUN1)


async def test_compiled_predicates_and_executor_enforce_real_unprivileged_rls(database) -> None:
    _, schema = database
    role = "cme_predicate_role_" + uuid4().hex
    expected = ({"v2p"}, {"v1pp"}, {"v1pp", "v2p"}, {"v1"})
    async with connect(database) as conn:
        await conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
        try:
            await conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role))
            )
            await conn.execute(
                sql.SQL("GRANT SELECT ON {}.memory_version, {}.scope_grant TO {}").format(
                    sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(role)
                )
            )
            await conn.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {}.clock_now() TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            async with conn.transaction():
                await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                cursor = await conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                assert await cursor.fetchone() == (False, False)
                for scoped_user in ("u1", "u2", ""):
                    await identity(conn, scoped_user, local=True)
                    for parameter_user in ("u1", "u2"):
                        for index, query in enumerate(ordinary_and_audit(parameter_user)):
                            alias = 'version "row"'
                            compiled = compile_predicate(query, alias=alias, database_clock=OCT1)
                            cursor = await conn.execute(
                                sql.SQL("SELECT id FROM {}.memory_version AS {} WHERE {}").format(
                                    sql.Identifier(schema), sql.Identifier(alias), compiled.statement
                                ),
                                compiled.parameters,
                            )
                            wanted = expected[index] if scoped_user == parameter_user == "u1" else set()
                            if scoped_user == parameter_user == "u2":
                                wanted = {"other-user"}
                            assert {row[0] for row in await cursor.fetchall()} == wanted
                            if scoped_user == parameter_user:
                                assert await selected(conn, schema, query) == wanted
                            else:
                                with pytest.raises(InsufficientPrivilege):
                                    await fetch_versions(conn, query, schema_name=schema)
                await identity(conn, "u1", local=True)
                cursor = await conn.execute("SELECT id FROM memory_version ORDER BY id")
                assert "other-user" not in {row[0] for row in await cursor.fetchall()}
        finally:
            await conn.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            await conn.execute(
                sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            await conn.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(sql.Identifier(schema), sql.Identifier(role))
            )
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def test_explicit_user_filter_remains_effective_when_connection_bypasses_rls(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        cursor = await conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert await cursor.fetchone() == (True,), (
            "the disposable fixture must provide the independent RLS-bypass control"
        )
        await identity(conn, "u1")
        expected = ({"v2p"}, {"v1pp"}, {"v1pp", "v2p"}, {"v1"})
        for query, wanted in zip(ordinary_and_audit("u1"), expected, strict=True):
            assert await selected(conn, schema, query) == wanted
        cursor = await conn.execute("SELECT count(*) FROM memory_version WHERE user_id='u2'")
        assert await cursor.fetchone() == (1,)
