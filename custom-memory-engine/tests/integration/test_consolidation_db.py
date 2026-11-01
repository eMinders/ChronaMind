"""RL-061: episodic -> semantic consolidation, Invariant 1 and branch (C) archival.

Each test builds a disposable schema from the canonical DDL, so these run against
the full 12-table schema rather than a reduced fixture. They need a database; they
skip with an explicit reason when ``CME_TEST_DATABASE_URL`` is absent.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.consolidation import (
    MAX_DERIVATION_DEPTH,
    ConsolidationError,
    consolidate,
    load_sources,
    plan_consolidation,
)
from custom_memory_engine.engine.transactions import (
    ArchivalPreconditionError,
    CandidateVersionInput,
    commit_initial_version,
)
from tests.integration.schema import apply_migrations

pytestmark = pytest.mark.integration

START = datetime(2026, 3, 10, tzinfo=UTC)
APRIL = datetime(2026, 4, 1, tzinfo=UTC)
JUNE = datetime(2026, 6, 1, tzinfo=UTC)
JULY = datetime(2026, 7, 20, tzinfo=UTC)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist")
    config = DatabaseConfig.from_environment()
    schema = "cme_cons_" + uuid4().hex[:16]
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await apply_migrations(conn)
        await conn.execute(
            "INSERT INTO predicate_schema VALUES "
            "('diet', 'single', 'semantic'), ('hobby', 'multi', 'persona'), ('city', 'single', 'semantic')"
        )
        await conn.execute(
            "INSERT INTO app_user (id, external_ref, created_at) VALUES "
            "('u1', 'ext-u1', '2026-01-01T00:00:00Z'), ('u2', 'ext-u2', '2026-01-01T00:00:00Z')"
        )
    try:
        yield config, schema
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@asynccontextmanager
async def connect(
    database: tuple[DatabaseConfig, str], *, user: str = "u1", clock: datetime = JULY
) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET timezone = 'UTC'")
        await conn.execute("SELECT set_config('app.user_id', %s, false)", (user,))
        await conn.execute("SELECT set_config('app.clock', %s, false)", (clock.isoformat(),))
        yield conn


async def scalar(conn: AsyncConnection, statement: str, params: tuple[object, ...] = ()) -> object:
    cursor = await conn.execute(cast(LiteralString, statement), params, prepare=False)
    row = await cursor.fetchone()
    return None if row is None else row[0]


async def seed_turn(conn: AsyncConnection, turn_id: str, content: str, at: datetime) -> None:
    await conn.execute(
        "INSERT INTO session (id, user_id, started_at) VALUES (%s, 'u1', %s) ON CONFLICT DO NOTHING",
        ("s1", START),
    )
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, 's1', 'u1', (SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM turn WHERE session_id = 's1'), "
        "'user', %s, %s)",
        (turn_id, content, at),
    )


def episodic(
    version_id: str,
    content: str,
    *,
    predicate: str = "diet",
    valid_from: datetime = START,
    valid_until: datetime | None = None,
    single_valued: bool = False,
) -> CandidateVersionInput:
    """An extracted episodic candidate, grounded in a user turn."""
    return CandidateVersionInput(
        version_id=version_id,
        memory_key=f"user:{predicate}",
        user_id="u1",
        subject_key="user",
        predicate_key=predicate,
        content=content,
        valid_from=valid_from,
        valid_until=valid_until,
        single_valued=single_valued,
        layer="episodic",
        evidence=(_evidence("t1", content),),
    )


def _evidence(turn_id: str, span: str):
    from custom_memory_engine.engine.transactions import EvidenceInput

    return EvidenceInput(turn_id=turn_id, evidence_span=span)


async def commit(conn: AsyncConnection, candidate: CandidateVersionInput, at: datetime, schema: str) -> None:
    async with conn.transaction():
        await commit_initial_version(conn, candidate, schema_name=schema, replay_time=at)


async def run_consolidation(
    conn: AsyncConnection, user_id: str, sources: tuple[str, ...], *, schema: str, at: datetime, **kwargs
):
    """``consolidate`` requires a caller-owned transaction, so the test opens one."""
    async with conn.transaction():
        return await consolidate(conn, user_id, sources, schema_name=schema, replay_time=at, **kwargs)


# --------------------------------------------------------------------- AC1


async def test_consolidation_links_sources_and_covers_their_event_ranges(database) -> None:
    """AC1: MEMORY_DERIVATION links exist and ``V(r') @> V(r)`` for every source."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian", valid_from=START), START, schema)
        await commit(conn, episodic("e2", "I still avoid meat", valid_from=APRIL), APRIL, schema)

        outcome = await run_consolidation(
            conn, "u1", ("e1", "e2"), content="The user is vegetarian", version_id="c1", schema=schema, at=JUNE
        )
        assert outcome.version_id == "c1"
        assert outcome.source_version_ids == ("e1", "e2")

        rows = await conn.execute(
            "SELECT derived_version_id, source_version_id, derivation_type FROM memory_derivation "
            "WHERE derived_version_id = 'c1' ORDER BY source_version_id"
        )
        assert [tuple(row) for row in await rows.fetchall()] == [
            ("c1", "e1", "consolidation"),
            ("c1", "e2", "consolidation"),
        ]

        # Coverage is the archival precondition, so assert it in SQL rather than in Python.
        for source_id in ("e1", "e2"):
            assert await scalar(
                conn,
                "SELECT event_range @> "
                "(SELECT event_range FROM memory_version WHERE id = %s) "
                "FROM memory_version WHERE id = 'c1'",
                (source_id,),
            )
        assert await scalar(conn, "SELECT status FROM memory_version WHERE id = 'c1'") == "active"
        assert await scalar(conn, "SELECT layer FROM memory_version WHERE id = 'c1'") == "semantic"


async def test_consolidated_version_satisfies_invariant_1_without_evidence(database) -> None:
    """AC2 (first half): a derived row carries derivation links and no evidence rows."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        await run_consolidation(conn, "u1", ("e1",), content="Vegetarian", version_id="c1", schema=schema, at=JUNE)
        assert await scalar(conn, "SELECT count(*) FROM memory_evidence WHERE version_id = 'c1'") == 0
        assert await scalar(conn, "SELECT count(*) FROM memory_derivation WHERE derived_version_id = 'c1'") == 1


async def test_multi_source_consolidation_spans_the_union_of_source_ranges(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian", valid_from=START, valid_until=APRIL), START, schema)
        await commit(conn, episodic("e2", "I avoid meat", valid_from=APRIL, valid_until=JUNE), APRIL, schema)
        outcome = await run_consolidation(
            conn, "u1", ("e1", "e2"), content="Vegetarian", version_id="c1", schema=schema, at=JULY
        )
        assert outcome.committed.valid_from == START
        assert outcome.committed.valid_until == JUNE
        # An open-ended source forces an open-ended consolidation.
        await commit(conn, episodic("e3", "I still avoid meat", valid_from=JUNE), JUNE, schema)
        sparse = await run_consolidation(
            conn, "u1", ("e1", "e3"), content="Vegetarian", version_id="c2", schema=schema, at=JULY
        )
        assert sparse.committed.valid_until is None


# --------------------------------------------------------------------- AC2


async def test_consolidation_enables_branch_c_archival(database) -> None:
    """AC2 (second half): a covering replacement makes branch (C) legal for its source."""
    from custom_memory_engine.engine.transactions import archive_version

    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        await run_consolidation(conn, "u1", ("e1",), content="Vegetarian", version_id="c1", schema=schema, at=JUNE)
        async with conn.transaction():
            archived = await archive_version(conn, user_id="u1", version_id="e1", schema_name=schema, replay_time=JULY)
        assert archived.archival_branch == "C"
        assert await scalar(conn, "SELECT status FROM memory_version WHERE id = 'e1'") == "archived"
        # The replacement still answers current-state queries.
        assert await scalar(
            conn, "SELECT status FROM memory_version WHERE id = 'c1' AND upper_inf(recorded_range)"
        ) == ("active")


async def test_archival_without_a_consolidation_is_still_rejected(database) -> None:
    """Consolidation is what unlocks (C); without it, T1/T2 still reject."""
    from custom_memory_engine.engine.transactions import archive_version

    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        async with conn.transaction():
            with pytest.raises(ArchivalPreconditionError, match="archival_precondition_unmet"):
                await archive_version(conn, user_id="u1", version_id="e1", schema_name=schema, replay_time=JUNE)


async def test_consolidation_does_not_archive_anything_by_itself(database) -> None:
    """Deriving a replacement must never silently retire the source."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        await run_consolidation(conn, "u1", ("e1",), content="Vegetarian", version_id="c1", schema=schema, at=JUNE)
        assert await scalar(conn, "SELECT status FROM memory_version WHERE id = 'e1'") == "active"


# --------------------------------------------------------------------- AC3


async def test_derivation_depth_is_enforced(database) -> None:
    """AC3: a chain longer than MAX_DERIVATION_DEPTH is refused before any write."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        current = "e1"
        for level in range(MAX_DERIVATION_DEPTH):
            target = f"d{level}"
            await run_consolidation(
                conn, "u1", (current,), content=f"level {level}", version_id=target, schema=schema, at=JUNE
            )
            current = target
        sources = await load_sources(conn, "u1", (current,), schema_name=schema)
        assert sources[0].depth == MAX_DERIVATION_DEPTH
        with pytest.raises(ConsolidationError, match="derivation_depth_exceeded"):
            await run_consolidation(
                conn, "u1", (current,), content="too deep", version_id="dX", schema=schema, at=JUNE
            )
        assert await scalar(conn, "SELECT count(*) FROM memory_version WHERE id = 'dX'") == 0


async def test_source_lineage_is_preserved_for_downstream_deletion(database) -> None:
    """AC3: RL-070 re-derivation needs the full source set, not just the last link."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian", valid_from=START, valid_until=APRIL), START, schema)
        await commit(conn, episodic("e2", "I avoid meat", valid_from=APRIL), APRIL, schema)
        await run_consolidation(
            conn, "u1", ("e1", "e2"), content="Vegetarian", version_id="c1", schema=schema, at=JUNE
        )
        rows = await conn.execute(
            "SELECT source_version_id FROM memory_derivation "
            "WHERE derived_version_id = 'c1' ORDER BY source_version_id"
        )
        assert [row[0] for row in await rows.fetchall()] == ["e1", "e2"]


# ------------------------------------------------------- refusal / safety


async def test_only_active_sources_may_be_consolidated(database) -> None:
    from custom_memory_engine.engine.transactions import archive_version

    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        await run_consolidation(conn, "u1", ("e1",), content="Vegetarian", version_id="c1", schema=schema, at=JUNE)
        async with conn.transaction():
            await archive_version(conn, user_id="u1", version_id="e1", schema_name=schema, replay_time=JULY)
        with pytest.raises(ConsolidationError, match="source_version_must_be_active"):
            await load_sources(conn, "u1", ("e1",), schema_name=schema)


async def test_unknown_source_is_refused_without_echoing_the_id(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        with pytest.raises(ConsolidationError) as failure:
            await load_sources(conn, "u1", ("does-not-exist",), schema_name=schema)
        assert "source_version_not_found" in str(failure.value)
        assert "does-not-exist" not in str(failure.value)


async def test_cross_tenant_sources_are_refused(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        async with connect(database, user="u2") as other:
            with pytest.raises(ConsolidationError, match="source_version_not_found"):
                await load_sources(other, "u2", ("e1",), schema_name=schema)


async def test_context_must_match_the_caller(database) -> None:
    _, schema = database
    async with connect(database, user="u2") as conn:
        with pytest.raises(Exception, match="consolidation_user_context_mismatch"):
            await load_sources(conn, "u1", ("e1",), schema_name=schema)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"content": "   "}, "nonblank_consolidated_content_required"),
        ({"layer": "episodic"}, "consolidation_layer_must_be_semantic"),
        ({"single_valued": "yes"}, "single_valued_flag_required"),
    ],
)
async def test_plan_rejects_invalid_requests(database, changes, expected) -> None:
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        sources = await load_sources(conn, "u1", ("e1",), schema_name=schema)
        arguments = {"user_id": "u1", "content": "Vegetarian", "version_id": "c1", **changes}
        with pytest.raises(ConsolidationError, match=expected):
            plan_consolidation(sources, **arguments)  # type: ignore[arg-type]


async def test_plan_refuses_sources_spanning_predicates(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        await commit(conn, episodic("e2", "I play chess", predicate="hobby"), START, schema)
        sources = await load_sources(conn, "u1", ("e1", "e2"), schema_name=schema)
        with pytest.raises(ConsolidationError, match="sources_must_share_subject_and_predicate"):
            plan_consolidation(sources, user_id="u1", content="mixed", version_id="c1")


async def test_version_id_may_not_collide_with_a_source(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        sources = await load_sources(conn, "u1", ("e1",), schema_name=schema)
        with pytest.raises(ConsolidationError, match="derived_version_must_differ_from_sources"):
            plan_consolidation(sources, user_id="u1", content="x", version_id="e1")


async def test_consolidation_leaves_no_audit_content(database) -> None:
    """Invariant 8 hygiene: the audit trail must stay content-free."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await commit(conn, episodic("e1", "I am vegetarian"), START, schema)
        await run_consolidation(conn, "u1", ("e1",), content="Vegetarian", version_id="c1", schema=schema, at=JUNE)
        details = await scalar(
            conn, "SELECT details::text FROM audit_event WHERE memory_ref = 'c1' AND event_type = 'version_committed'"
        )
        assert details is not None and "Vegetarian".lower() not in str(details).lower()
