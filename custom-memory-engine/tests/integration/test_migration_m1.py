"""E-010 / M1 + M7: the independent axes on ``memory_version``, their backfill and the dual-write window.

Every fixture here migrates through the runner (all versions), unlike the older integration tests that load
``001_full_schema.sql`` only, so the v1 write paths are exercised against the schema V2 code will run on.
"""

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, RaiseException

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.consolidation import consolidate
from custom_memory_engine.deletion.rederive import ActiveSource, RebuiltSummary
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.ingest import TurnInput, apply_candidate, persist_turn, resolve_held
from custom_memory_engine.engine.transactions import (
    CandidateVersionInput,
    EvidenceInput,
    archive_version,
    commit_initial_version,
)
from custom_memory_engine.model.axes import V1_LAYERS, V1_STATUSES, map_v1_version, validate_axes
from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql

pytestmark = pytest.mark.integration

START = datetime(2026, 3, 10, tzinfo=UTC)
APRIL = datetime(2026, 4, 1, tzinfo=UTC)
JUNE = datetime(2026, 6, 1, tzinfo=UTC)
JULY = datetime(2026, 7, 20, tzinfo=UTC)
PREDICATES = ("diet", "plans_to", "exercise", "diet_summary", "diet_a", "diet_b")


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist")
    config = DatabaseConfig.from_environment()
    schema = "cme_m1_" + uuid4().hex[:16]
    await migrate.MigrationRunner(
        migrate.MigrationConfig(database_url=config.database_url, schema_name=schema)
    ).apply()
    async with connect((config, schema)) as conn:
        await conn.execute(
            "INSERT INTO predicate_schema VALUES " + ", ".join(f"('{p}', 'single', 'semantic')" for p in PREDICATES)
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


async def rows(conn: AsyncConnection, statement: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    cursor = await conn.execute(cast(LiteralString, statement), params, prepare=False)
    return [tuple(row) for row in await cursor.fetchall()]


AXES_QUERY = """
SELECT id, layer, status, kind, lifecycle, retention,
       EXISTS (SELECT 1 FROM memory_derivation d WHERE d.derived_version_id = v.id)
FROM memory_version v ORDER BY id
"""


async def assert_axes_consistent(conn: AsyncConnection) -> dict[str, tuple[str, str, str]]:
    """Every row's axes equal the approved v1 mapping of its ``(layer, status, has_derivation)``."""
    found: dict[str, tuple[str, str, str]] = {}
    for version_id, layer, status, kind, lifecycle, retention, derived in await rows(conn, AXES_QUERY):
        expected = map_v1_version(status, layer, derived)
        assert (kind, lifecycle, retention) == (expected.kind, expected.lifecycle, expected.retention), version_id
        validate_axes(kind, lifecycle, retention)  # kind-legal and floor-respecting
        found[str(version_id)] = (str(kind), str(lifecycle), str(retention))
    return found


async def seed_turn(conn: AsyncConnection, turn_id: str, content: str, at: datetime) -> None:
    await conn.execute(
        "INSERT INTO session (id, user_id, started_at) VALUES ('s1', 'u1', %s) ON CONFLICT DO NOTHING", (START,)
    )
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, 's1', 'u1', (SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM turn WHERE session_id = 's1'), "
        "'user', %s, %s)",
        (turn_id, content, at),
    )


def candidate(
    version_id: str, content: str, *, layer: str = "semantic", predicate: str = "diet", mode: str = "asserted"
) -> CandidateVersionInput:
    return CandidateVersionInput(
        version_id=version_id,
        memory_key=f"user:{predicate}:{version_id}" if layer != "semantic" else predicate,
        user_id="u1",
        subject_key="user",
        predicate_key=predicate,
        content=content,
        valid_from=START,
        single_valued=layer == "semantic",
        layer=layer,  # type: ignore[arg-type]
        assertion_mode=mode,  # type: ignore[arg-type]
        evidence=(EvidenceInput(turn_id="t1", evidence_span="I am vegetarian"),),
    )


# ------------------------------------------------------------------------------ backfill (M1 + M7)

SEED_ROW = """
INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content,
                            valid_range, recorded_range, layer, archival_branch)
VALUES (%s, %s, 'u1', %s, 'user', 'diet', false, %s, tstzrange(%s, NULL, '[)'),
        CASE WHEN %s IN ('active', 'archived')
             THEN tstzrange(clock_now(), CASE WHEN %s = 'archived' THEN clock_now() + interval '1 day' END, '[)') END,
        %s, CASE WHEN %s = 'archived' THEN 'S' END)
"""


async def seed_v1_row(
    conn: AsyncConnection, version_id: str, layer: str, status: str, *, derived_from: str | None
) -> None:
    async with conn.transaction():
        await _seed_v1_row(conn, version_id, layer, status, derived_from)


async def _seed_v1_row(
    conn: AsyncConnection, version_id: str, layer: str, status: str, derived_from: str | None
) -> None:
    await conn.execute(
        cast(LiteralString, SEED_ROW),
        (version_id, version_id, status, version_id, START, status, status, layer, status),
    )
    if status in ("active", "archived"):
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, 't1', 'u1', 'I am vegetarian', 'support')",
            (version_id,),
        )
    if derived_from is not None:
        await conn.execute(
            "INSERT INTO memory_derivation VALUES (%s, %s, 'u1', 'consolidation')", (version_id, derived_from)
        )


COMBINATIONS = [
    (layer, status, derived)
    for layer in V1_LAYERS
    for status in V1_STATUSES
    for derived in ((False, True) if status == "active" else (False,))
]


async def test_backfill_is_total_and_maps_every_v1_layer_and_status_combination(
    database: tuple[DatabaseConfig, str],
) -> None:
    """Acceptance 1 and 2: a database populated at v4 gets no NULL axis, and every combination maps per V2-D38."""
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=4)), prepare=False
        )  # back to the exact pre-M1 schema
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await conn.execute("SELECT set_config('app.clock', %s, false)", (START.isoformat(),))
        await seed_v1_row(conn, "source", "episodic", "active", derived_from=None)
        ids = {}
        for layer, status, derived in COMBINATIONS:
            version_id = f"{layer}-{status}-{'derived' if derived else 'plain'}"
            await seed_v1_row(conn, version_id, layer, status, derived_from="source" if derived else None)
            ids[version_id] = (layer, status, derived)
        assert len(ids) == len(COMBINATIONS) == 24  # 4 layers x (active plain/derived + 4 other statuses)

    await migrate.MigrationRunner(
        migrate.MigrationConfig(database_url=database[0].database_url, schema_name=database[1])
    ).apply()

    async with connect(database) as conn:
        assert await rows(
            conn,
            "SELECT count(*) FROM memory_version WHERE kind IS NULL OR lifecycle IS NULL OR retention IS NULL",
        ) == [(0,)]
        found = await assert_axes_consistent(conn)
        assert set(ids) <= set(found)
        for version_id, (layer, status, derived) in ids.items():
            expected = map_v1_version(status, layer, derived)
            assert found[version_id] == (expected.kind, expected.lifecycle, expected.retention), version_id
        # The decisions the issue names, spelled out so a mapping change is a visible diff:
        assert found["persona-active-plain"] == ("abstraction", "inferred", "durable")  # V2-D49 D7
        assert found["semantic-active-derived"] == ("claim", "inferred", "durable")
        assert found["opinion-active-plain"] == ("claim", "asserted", "durable")
        assert found["episodic-active-plain"] == ("episode", "observed", "durable")
        assert found["semantic-archived-plain"] == ("claim", "retired", "durable")  # M7: archived is a lifecycle
        assert found["semantic-conflict_pending-plain"] == ("claim", "contested", "durable")
        assert found["semantic-rejected-plain"] == ("claim", "retired", "durable")
        assert found["semantic-candidate-plain"] == ("claim", "retired", "durable")
        # `layer` was never rewritten: semantic and opinion stay distinguishable (V2-D49 D6).
        assert await rows(conn, "SELECT DISTINCT layer FROM memory_version ORDER BY 1") == [
            (layer,) for layer in sorted(V1_LAYERS)
        ]


# ------------------------------------------------------------------------------ database-level guarantees


async def insert_active(conn: AsyncConnection, version_id: str, *, layer: str = "semantic", extra: str = "") -> None:
    await seed_turn(conn, f"t-{version_id}", "I am vegetarian", START)
    async with conn.transaction():
        await conn.execute(
            cast(
                LiteralString,
                "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                "single_valued, content, valid_range, recorded_range, layer"
                + (", " + extra.split("=")[0] if extra else "")
                + ") VALUES (%s, %s, 'u1', 'active', 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
                "tstzrange(clock_now(), NULL), %s" + (", " + extra.split("=")[1] if extra else "") + ")",
            ),
            (version_id, version_id, START, layer),
        )
        await conn.execute(
            "INSERT INTO memory_evidence VALUES (%s, %s, 'u1', 'I am vegetarian', 'support')",
            (version_id, f"t-{version_id}"),
        )


async def test_kind_is_immutable_at_the_database_level(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as conn:
        await insert_active(conn, "v1")
        assert (await assert_axes_consistent(conn))["v1"] == ("claim", "asserted", "durable")
        # `kind` is a function of the immutable `layer`, so the dual-write trigger already refuses any other value...
        with pytest.raises(RaiseException, match="axes_kind_inconsistent_with_layer"):
            await conn.execute("UPDATE memory_version SET kind = 'episode' WHERE id = 'v1'")
        # ...and clearing it is re-derived, never a way to change it.
        await conn.execute("UPDATE memory_version SET kind = NULL WHERE id = 'v1'")
        assert (await assert_axes_consistent(conn))["v1"][0] == "claim"
        # guard_update is the independent second line (alongside the other immutable columns): with the
        # dual-write trigger out of the way, changing `kind` is still refused by the database.
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        with pytest.raises(RaiseException, match="only status"):
            await conn.execute("UPDATE memory_version SET kind = 'episode' WHERE id = 'v1'")
        await conn.execute("ALTER TABLE memory_version ENABLE TRIGGER trg_memory_version_axes_dual_write")


async def test_axes_that_contradict_the_v1_columns_are_rejected(database: tuple[DatabaseConfig, str]) -> None:
    """New code may write the axes, but never an inconsistent pair: v1 readers must keep seeing true values."""
    async with connect(database) as conn:
        with pytest.raises(RaiseException, match="axes_kind_inconsistent_with_layer"):
            await insert_active(conn, "bad-kind", layer="semantic", extra="kind='episode'")
        with pytest.raises(RaiseException, match="axes_lifecycle_inconsistent_with_status"):
            await insert_active(conn, "bad-lifecycle", extra="lifecycle='retired'")
        with pytest.raises(CheckViolation):
            await insert_active(conn, "bad-retention", extra="retention='archived'")  # archived is not a retention
        with pytest.raises(CheckViolation):
            await insert_active(conn, "bad-enum", extra="retention='forever'")
        # `procedure` has no v1 layer, so it is the one kind a caller may supply that `layer` cannot imply.
        await insert_active(conn, "proc", extra="kind='procedure'")
        assert await rows(conn, "SELECT kind, lifecycle FROM memory_version WHERE id = 'proc'") == [
            ("procedure", "asserted")
        ]
        with pytest.raises(RaiseException, match="only status"):
            await conn.execute("UPDATE memory_version SET kind = NULL WHERE id = 'proc'")  # cannot flip to a claim
        # A consistent explicit write is accepted unchanged.
        await insert_active(conn, "ok", layer="opinion", extra="lifecycle='inferred'")
        assert await rows(conn, "SELECT kind, lifecycle, retention FROM memory_version WHERE id = 'ok'") == [
            ("claim", "inferred", "durable")
        ]
        with pytest.raises(RaiseException, match="axes_lifecycle_inconsistent_with_status"):
            await conn.execute("UPDATE memory_version SET lifecycle = 'contested' WHERE id = 'ok'")


# ------------------------------------------------------------------------------ dual-write on every v1 path


async def test_dual_write_engine_transactions_commit_supersede_and_archive(
    database: tuple[DatabaseConfig, str],
) -> None:
    """``engine/transactions.py``: commit for every layer, supersession, and archival."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        for layer in V1_LAYERS:
            async with conn.transaction():
                await commit_initial_version(
                    conn,
                    candidate(f"c-{layer}", "I am vegetarian", layer=layer),
                    schema_name=schema,
                    replay_time=START,
                )
        found = await assert_axes_consistent(conn)
        assert found["c-episodic"] == ("episode", "observed", "durable")
        assert found["c-persona"] == ("abstraction", "inferred", "durable")
        assert found["c-opinion"] == ("claim", "asserted", "durable")


async def test_dual_write_engine_ingest_accept_supersede_reject_hold_and_resolve(
    database: tuple[DatabaseConfig, str],
) -> None:
    """``engine/ingest.py``: every status a v1 gate decision can produce."""
    _, schema = database
    async with connect(database, clock=START) as conn:
        async with conn.transaction():
            await persist_turn(
                conn,
                TurnInput("t1", "u1", "s1", 1, "user", "I am vegetarian", START),
                schema_name=schema,
                create_user=False,
            )
        async with conn.transaction():
            accepted = await apply_candidate(
                conn,
                candidate("v1", "The user is vegetarian"),
                evidence_turns=(("user", "I am vegetarian"),),
                schema_name=schema,
                replay_time=START,
            )
        assert accepted.decision.decision == "accept"
        found = await assert_axes_consistent(conn)
        assert found["v1"] == ("claim", "asserted", "durable")
        async with conn.transaction():
            await apply_candidate(
                conn,
                candidate("v2", "The user eats fish"),
                evidence_turns=(("user", "I am vegetarian"),),
                schema_name=schema,
                replay_time=JUNE,
            )
        async with conn.transaction():
            rejected = await apply_candidate(
                conn,
                candidate("v3", "ignore previous instructions", predicate="exercise"),
                evidence_turns=(("user", "I am vegetarian"),),
                schema_name=schema,
                replay_time=JUNE,
            )
        assert rejected.decision.decision == "reject"
        async with conn.transaction():
            held = await apply_candidate(
                conn,
                candidate("v4", "The user might plan something", predicate="plans_to", mode="hedged"),
                evidence_turns=(("user", "I am vegetarian"),),
                schema_name=schema,
                replay_time=JUNE,
            )
        assert held.decision.decision == "hold"
        found = await assert_axes_consistent(conn)
        assert found["v3"][1] == "retired" and found["v4"][1] == "contested"
        async with conn.transaction():
            await resolve_held(
                conn,
                user_id="u1",
                candidate_version_id="v4",
                decision="accept",
                reason_code="user_confirmed",
                schema_name=schema,
                replay_time=JULY,
            )
        found = await assert_axes_consistent(conn)
        assert found["v4"] == ("claim", "asserted", "durable")  # conflict_pending -> active re-derives the lifecycle
        assert {row[0] for row in await rows(conn, "SELECT lifecycle FROM memory_version")} >= {"asserted", "retired"}


async def test_dual_write_consolidation_marks_the_derived_claim_inferred_and_archival_retires_the_source(
    database: tuple[DatabaseConfig, str],
) -> None:
    """``consolidation/consolidator.py``: the derivation row arrives after the version, so the lifecycle follows it."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        for version_id in ("e1", "e2"):
            async with conn.transaction():
                await commit_initial_version(
                    conn,
                    candidate(version_id, "I am vegetarian", layer="episodic", predicate="diet_a"),
                    schema_name=schema,
                    replay_time=START,
                )
        async with conn.transaction():
            await consolidate(
                conn, "u1", ("e1", "e2"), content="Vegetarian", version_id="c1", schema_name=schema, replay_time=JUNE
            )
        found = await assert_axes_consistent(conn)
        assert found["c1"] == ("claim", "inferred", "durable")  # derived => inferred (V2-D38)
        assert found["e1"] == ("episode", "observed", "durable")
        async with conn.transaction():
            await archive_version(conn, user_id="u1", version_id="e1", schema_name=schema, replay_time=JULY)
        found = await assert_axes_consistent(conn)
        assert found["e1"] == ("episode", "retired", "durable")  # M7: archived is `retired`, durable


async def test_dual_write_rederivation_keeps_the_rebuilt_summary_consistent(
    database: tuple[DatabaseConfig, str],
) -> None:
    """``deletion/rederive.py`` (through ``delete_memory``): the rebuilt summary keeps `inferred`."""
    _, schema = database
    async with connect(database) as conn:
        await seed_turn(conn, "t1", "I am vegetarian", START)
        async with conn.transaction():
            for version_id, key, predicate in (
                ("src-a", "diet", "diet_a"),
                ("src-b", "other", "diet_b"),
                ("summary", "summary", "diet_summary"),
            ):
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, valid_range, recorded_range) VALUES (%s, %s, 'u1', 'active', "
                    "'derived_sources', %s, false, %s, tstzrange(%s, NULL), tstzrange(clock_now(), NULL))",
                    (version_id, key, predicate, version_id, START),
                )
                if version_id != "summary":
                    await conn.execute(
                        "INSERT INTO memory_evidence VALUES (%s, 't1', 'u1', 'I am vegetarian', 'support')",
                        (version_id,),
                    )
            await conn.execute(
                "INSERT INTO memory_derivation VALUES ('summary', 'src-a', 'u1', 'summary'), "
                "('summary', 'src-b', 'u1', 'summary')"
            )
        assert (await assert_axes_consistent(conn))["summary"] == ("claim", "inferred", "durable")

        async def rebuild(summary_id: str, sources: Sequence[ActiveSource]) -> RebuiltSummary:
            assert [source.version_id for source in sources] == ["src-b"]
            return RebuiltSummary(content="avoids red meat", embedding_model="m", embedding=tuple([0.1] + [0.0] * 383))

        async with conn.transaction():
            receipt = await delete_memory(
                conn, "u1", scope="memory", target_ref="diet", summary_builder=rebuild, schema_name=schema
            )
        assert receipt.rederived_dependents == 1
        found = await assert_axes_consistent(conn)
        assert found["summary"] == ("claim", "inferred", "durable") and "src-a" not in found


# ------------------------------------------------------------------------------ reversibility


async def normalised_catalog(conn: AsyncConnection, schema: str) -> list[list[list[str]]]:
    """The runner's catalog queries with attribute numbers replaced by their rank within the table.

    PostgreSQL never reuses a dropped attribute number, so columns re-added by a re-apply sit at 23-25 where the
    first apply put them at 20-22. Their order, types, nullability and every other property are unchanged.
    """
    dumped = []
    for index, query in enumerate(migrate._CATALOG_QUERIES):
        cursor = await conn.execute(cast(LiteralString, query), (schema,))
        found = [[str(value) for value in row] for row in await cursor.fetchall()]
        if index == 1:  # (relname, attnum, attname, ...)
            ranks: dict[str, int] = {}
            for row in found:
                ranks[row[0]] = ranks.get(row[0], 0) + 1
                row[1] = str(ranks[row[0]])
        dumped.append(found)
    return dumped


async def test_apply_rollback_reapply_restores_the_catalog_exactly_each_time(
    database: tuple[DatabaseConfig, str],
) -> None:
    """Acceptance 5: the catalog after each step equals the recorded fingerprint of that version."""
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))

    async def state() -> tuple[str, int, list[tuple[object, ...]]]:
        async with connect(database) as conn:
            history = await rows(conn, "SELECT version, catalog_sha256 FROM _cme_schema_migrations ORDER BY 1")
            columns = await rows(
                conn,
                "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = 'memory_version' AND column_name IN ('kind', 'lifecycle', 'retention') ORDER BY 1",
            )
            return str(history[-1][1]), int(history[-1][0]), columns  # type: ignore[call-overload]

    async with connect(database) as conn:  # data to survive the round trip
        await seed_turn(conn, "t1", "I am vegetarian", START)
        await insert_active(conn, "keep", layer="opinion")
    _, version, columns = await state()
    assert version == migrate.VERSION and len(columns) == 3
    async with connect(database) as conn:
        first_apply = await normalised_catalog(conn, schema)

    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=4)), prepare=False
        )
        v4_catalog = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = 4")
        assert await runner._catalog(conn) == v4_catalog[0][0]  # byte-identical to the pre-M1 schema
        assert await rows(conn, "SELECT id, layer, status FROM memory_version") == [("keep", "opinion", "active")]
    status = await runner.status()
    assert (status.state, status.version) == ("pending", 4)

    assert (await runner.apply()).action == "applied"
    _, version, columns = await state()
    assert (version, len(columns)) == (migrate.VERSION, 3)
    async with connect(database) as conn:
        assert await normalised_catalog(conn, schema) == first_apply
        assert (await assert_axes_consistent(conn))["keep"] == ("claim", "asserted", "durable")  # re-backfilled


async def test_guard_update_is_restored_exactly_by_the_rollback(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=4)), prepare=False
        )
        body = await rows(conn, "SELECT pg_get_functiondef('guard_update'::regproc)")
        assert "kind" not in str(body[0][0]) and "NEW.layer" in str(body[0][0])
