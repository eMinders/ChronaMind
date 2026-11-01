"""E-016: the lifecycle transition table is closed and enforced by PostgreSQL for every writer (ARCHITECTURE_V2 2.3).

The oracle is ``model.axes`` (``TRANSITIONS`` and the kind-conditioned ``LEGAL_LIFECYCLE``): the database must accept
exactly what the model accepts, for all four kinds and all 5 x 5 ``(from, to)`` pairs.
"""

from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, InvalidParameterValue, RaiseException

from custom_memory_engine.model.axes import (
    KINDS,
    LEGAL_LIFECYCLE,
    LIFECYCLES,
    AxisError,
    Kind,
    Lifecycle,
    require_transition,
)
from custom_memory_engine.schema import migrate
from custom_memory_engine.security import provision_runtime_role
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
    seed_turn,
)

pytestmark = pytest.mark.integration
M6 = 6
LAYER_OF: dict[str, str] = {
    "episode": "episodic",
    "claim": "semantic",
    "procedure": "semantic",
    "abstraction": "persona",
}
#: The v1 status each lifecycle is stored with (migration 4), so a seeded row is coherent for v1 readers.
STATUS_OF: dict[str, str] = {
    "observed": "active",
    "asserted": "active",
    "inferred": "active",
    "contested": "conflict_pending",
    "retired": "rejected",
}
ALLOWED = {
    ("contested", "asserted"),
    ("contested", "retired"),
    ("asserted", "retired"),
    ("observed", "retired"),
    ("inferred", "retired"),
    ("inferred", "contested"),
}


def oracle(kind: Kind, source: Lifecycle, target: Lifecycle) -> bool:
    """Does the model accept ``source -> target`` for ``kind``? (identity is not a transition: nothing changes)"""
    if source == target:
        return True
    try:
        require_transition(source, target, kind)
    except AxisError:
        return False
    return True


async def seed_source(conn: AsyncConnection) -> None:
    """One believed claim that derived rows can cite."""
    await seed_turn(conn, "t0", "I am vegetarian", START)
    await insert_version(conn, "src", "claim", "asserted")


async def insert_version(conn: AsyncConnection, version_id: str, kind: str, lifecycle: str, **extra: str) -> None:
    status = extra.pop("status", STATUS_OF[lifecycle])
    believed = status in ("active", "archived")
    async with conn.transaction():  # the provenance check is deferred to commit; evidence goes in the same one
        await _insert_version(conn, version_id, kind, lifecycle, status, believed)


async def _insert_version(
    conn: AsyncConnection, version_id: str, kind: str, lifecycle: str, status: str, believed: bool
) -> None:
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
        "content, valid_range, recorded_range, layer, kind, lifecycle, retention) "
        "VALUES (%s, %s, 'u1', %s, 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
        "CASE WHEN %s THEN tstzrange(clock_now(), NULL) END, %s, %s, %s, 'durable')",
        (version_id, version_id, status, START, believed, LAYER_OF[kind], kind, lifecycle),
    )
    if status == "active":
        await conn.execute(
            "INSERT INTO memory_evidence VALUES (%s, 't0', 'u1', 'I am vegetarian', 'support')", (version_id,)
        )
        if lifecycle == "inferred":
            await conn.execute(
                "INSERT INTO memory_derivation VALUES (%s, 'src', 'u1', 'consolidation')", (version_id,)
            )


# --------------------------------------------------------------------------- the exhaustive matrix


async def test_every_kind_and_every_lifecycle_pair_matches_the_model(database) -> None:  # noqa: F811
    """4 kinds x 5 x 5 pairs (plus creation): exactly the model's transitions succeed, the rest raise a static code.

    The dual-write trigger is switched off for the matrix only, so status/lifecycle coherence (migration 4, tested
    elsewhere) does not mask the lifecycle guard under test. Each case runs in a transaction that is rolled back.
    """
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        succeeded: set[tuple[str, str, str]] = set()
        for kind in KINDS:
            for source in LIFECYCLES:
                legal_source = source in LEGAL_LIFECYCLE[kind]
                async with conn.transaction(force_rollback=True):
                    if not legal_source:
                        with pytest.raises(CheckViolation, match="lifecycle_legal_for_kind"):
                            await insert_version(conn, "row", kind, source)
                        continue
                for target in LIFECYCLES:
                    if not legal_source:
                        continue
                    async with conn.transaction(force_rollback=True):
                        await insert_version(conn, "row", kind, source)
                        expected = oracle(kind, source, target)
                        try:
                            async with conn.transaction():
                                await conn.execute(
                                    "UPDATE memory_version SET lifecycle = %s WHERE id = 'row'", (target,)
                                )
                        except (RaiseException, CheckViolation) as error:
                            assert not expected, f"{kind}: {source} -> {target} is allowed by the model but raised"
                            message = str(error)
                            assert "SECRET" not in message and "'x'" not in message  # content-free
                            continue
                        assert expected, f"{kind}: {source} -> {target} succeeded but the model forbids it"
                        succeeded.add((kind, source, target))
    # and the accepted set is exactly the table (+ identity), restricted to what each kind may hold
    for _kind, source, target in succeeded:
        assert source == target or (source, target) in ALLOWED
    for kind in KINDS:
        for source, target in ALLOWED:
            both_legal = source in LEGAL_LIFECYCLE[kind] and target in LEGAL_LIFECYCLE[kind]
            assert ((kind, source, target) in succeeded) == both_legal, (kind, source, target)


async def test_each_forbidden_transition_raises_its_own_static_code(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        cases = [
            ("claim", "inferred", "asserted", "lifecycle_inferred_to_asserted_unrepresentable"),
            ("claim", "retired", "asserted", "lifecycle_retired_is_terminal"),
            ("claim", "retired", "contested", "lifecycle_retired_is_terminal"),
            ("claim", "retired", "inferred", "lifecycle_retired_is_terminal"),
            ("claim", "observed", "asserted", "lifecycle_transition_not_permitted"),
            ("claim", "asserted", "contested", "lifecycle_transition_not_permitted"),
            ("claim", "contested", "inferred", "lifecycle_transition_not_permitted"),
            ("claim", "asserted", "observed", "lifecycle_transition_not_permitted"),
            ("abstraction", "contested", "asserted", "lifecycle_not_legal_for_kind"),  # C-P1: adjudicate(confirm)
            ("episode", "contested", "asserted", "lifecycle_not_legal_for_kind"),
        ]
        for kind, source, target, code in cases:
            async with conn.transaction(force_rollback=True):
                await insert_version(conn, "row", kind, source)
                with pytest.raises(RaiseException, match=code):
                    await conn.execute("UPDATE memory_version SET lifecycle = %s WHERE id = 'row'", (target,))


async def test_creation_is_open_only_for_the_four_entry_lifecycles(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        for lifecycle in ("observed", "asserted", "inferred", "contested"):
            async with conn.transaction(force_rollback=True):
                await insert_version(conn, "row", "claim", lifecycle)
        # `retired` at creation exists only for v1 staging rows (dual-write window)
        async with conn.transaction(force_rollback=True):
            await insert_version(conn, "staged", "claim", "retired")  # status rejected
        async with conn.transaction(force_rollback=True):
            await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
            with pytest.raises(RaiseException, match="lifecycle_creation_not_permitted"):
                await insert_version(conn, "born-retired", "claim", "retired", status="active")


# --------------------------------------------------------------------------- the dual-write window allowances


async def test_a_derived_claim_may_become_inferred_only_when_a_derivation_row_exists(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        async with conn.transaction(force_rollback=True):
            await insert_version(conn, "row", "claim", "asserted")
            with pytest.raises(RaiseException, match="lifecycle_transition_not_permitted"):
                await conn.execute("UPDATE memory_version SET lifecycle = 'inferred' WHERE id = 'row'")
        async with conn.transaction(force_rollback=True):
            await insert_version(conn, "row", "claim", "asserted")
            await conn.execute("INSERT INTO memory_derivation VALUES ('row', 'src', 'u1', 'consolidation')")
            await conn.execute("UPDATE memory_version SET lifecycle = 'inferred' WHERE id = 'row'")


async def test_a_retired_belief_cannot_be_resurrected_but_a_staged_candidate_can_be_promoted(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        async with conn.transaction(force_rollback=True):
            await insert_version(conn, "gone", "claim", "retired", status="rejected")
            with pytest.raises(RaiseException, match="lifecycle_retired_is_terminal"):
                await conn.execute("UPDATE memory_version SET lifecycle = 'asserted' WHERE id = 'gone'")
        async with conn.transaction(force_rollback=True):
            await insert_version(conn, "staged", "claim", "retired", status="candidate")
            await conn.execute("UPDATE memory_version SET lifecycle = 'asserted' WHERE id = 'staged'")


async def test_re_asserting_retired_content_creates_a_new_artifact(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await insert_version(conn, "old", "claim", "asserted")
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        async with conn.transaction():
            await conn.execute("UPDATE memory_version SET lifecycle = 'retired' WHERE id = 'old'")
        with pytest.raises(RaiseException, match="lifecycle_retired_is_terminal"):
            await conn.execute("UPDATE memory_version SET lifecycle = 'asserted' WHERE id = 'old'")
        await conn.execute("ALTER TABLE memory_version ENABLE TRIGGER trg_memory_version_axes_dual_write")
        async with conn.transaction():
            await insert_version(conn, "new", "claim", "asserted")  # same content, new id
        assert await rows(conn, "SELECT id, lifecycle FROM memory_version WHERE id IN ('old', 'new') ORDER BY 1") == [
            ("new", "asserted"),
            ("old", "retired"),
        ]


# --------------------------------------------------------------------------- a real application role, raw SQL


async def test_raw_sql_under_a_non_superuser_application_role_cannot_promote_an_inference(database) -> None:  # noqa: F811
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    try:
        async with connect(database) as admin:
            await provision_runtime_role(admin, schema, role)
            await seed_source(admin)
        async with connect(database) as app:
            await app.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            (is_super,) = (await rows(app, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))[0]
            assert is_super is False
            async with app.transaction():
                await insert_version(app, "hypothesis", "claim", "inferred")
            with pytest.raises(RaiseException, match="lifecycle_inferred_to_asserted_unrepresentable"):
                await app.execute("UPDATE memory_version SET lifecycle = 'asserted' WHERE id = 'hypothesis'")
            with pytest.raises(RaiseException, match="lifecycle_inferred_to_asserted_unrepresentable"):
                await app.execute(
                    "UPDATE memory_version SET lifecycle = 'asserted', status = 'active' WHERE id = 'hypothesis'"
                )
            assert await rows(app, "SELECT lifecycle FROM memory_version WHERE id = 'hypothesis'") == [("inferred",)]
            # the trigger cannot be switched off by the role either (it does not own the table)
            with pytest.raises(Exception, match="must be owner|permission denied"):
                await app.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_lifecycle_guard")
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


# --------------------------------------------------------------------------- records and the I1' guard


async def test_an_inferred_row_without_a_derivation_fails_at_commit(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        with pytest.raises(RaiseException, match="inferred_requires_derivation_record"):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, valid_range, recorded_range, layer, kind, lifecycle, retention) "
                    "VALUES ('h', 'h', 'u1', 'active', 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
                    "tstzrange(clock_now(), NULL), 'semantic', 'claim', 'inferred', 'durable')",
                    (START,),
                )
                await conn.execute("INSERT INTO memory_evidence VALUES ('h', 't0', 'u1', 'x', 'support')")
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE id = 'h'") == [(0,)]
        async with conn.transaction():  # with its derivation it commits
            await insert_version(conn, "h", "claim", "inferred")


async def test_an_asserted_or_observed_row_needs_an_evidence_record_at_commit(database) -> None:  # noqa: F811
    """A derivation alone satisfies v1 Invariant 1, but it is not grounding for an asserted or observed row."""
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        await conn.execute("ALTER TABLE memory_derivation DISABLE TRIGGER trg_memory_derivation_mark_inferred")
        for kind, lifecycle in (("claim", "asserted"), ("claim", "observed"), ("episode", "observed")):
            with pytest.raises(RaiseException, match="lifecycle_requires_evidence_record"):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                        "single_valued, content, valid_range, recorded_range, layer, kind, lifecycle, retention) "
                        "VALUES ('row', 'row', 'u1', 'active', 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
                        "tstzrange(clock_now(), NULL), %s, %s, %s, 'durable')",
                        (START, LAYER_OF[kind], kind, lifecycle),
                    )
                    await conn.execute("INSERT INTO memory_derivation VALUES ('row', 'src', 'u1', 'summary')")
            assert await rows(conn, "SELECT count(*) FROM memory_version WHERE id = 'row'") == [(0,)]


async def test_a_v1_persona_row_is_the_documented_exemption_from_the_derivation_record(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        async with conn.transaction():
            await insert_version(conn, "persona", "abstraction", "contested")
            await conn.execute("INSERT INTO memory_evidence VALUES ('persona', 't0', 'u1', 'x', 'support')")
            await conn.execute(  # the v1 path that accepts a held candidate
                "UPDATE memory_version SET status = 'active', recorded_range = tstzrange(clock_now(), NULL) "
                "WHERE id = 'persona'"
            )
        assert await rows(conn, "SELECT kind, lifecycle FROM memory_version WHERE id = 'persona'") == [
            ("abstraction", "inferred")
        ]
        async with conn.transaction():
            await insert_version(conn, "persona-2", "abstraction", "inferred")  # evidence + derivation also fine


async def test_retiring_through_a_status_change_still_needs_the_v1_archival_precondition(database) -> None:  # noqa: F811
    """asserted -> retired arrives as status 'archived', so migration 1's (S)-or-(C) guard still applies."""
    async with connect(database) as conn:
        await seed_source(conn)
        async with conn.transaction():
            await insert_version(conn, "r", "claim", "asserted")
        with pytest.raises(InvalidParameterValue, match="archival_precondition_unmet"):
            await conn.execute("UPDATE memory_version SET status = 'archived', archival_branch = 'S' WHERE id = 'r'")
        assert await rows(conn, "SELECT status, lifecycle FROM memory_version WHERE id = 'r'") == [
            ("active", "asserted")
        ]
        await conn.execute("SELECT set_config('app.clock', %s, false)", ("2026-09-01T00:00:00+00:00",))
        await conn.execute(
            "UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) WHERE id = 'r'"
        )
        await conn.execute("UPDATE memory_version SET status = 'archived', archival_branch = 'S' WHERE id = 'r'")
        assert await rows(conn, "SELECT status, lifecycle FROM memory_version WHERE id = 'r'") == [
            ("archived", "retired")
        ]
        with pytest.raises(RaiseException, match="lifecycle_retired_is_terminal"):
            await conn.execute("UPDATE memory_version SET status = 'active' WHERE id = 'r'")


# --------------------------------------------------------------------------- rollback


async def test_rollback_restores_version_5_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M6 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M6 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical to the pre-guard schema
        # with the guard gone, the very transition the guard forbids is representable again
        await insert_version(conn, "h", "claim", "inferred")
        await conn.execute("UPDATE memory_version SET lifecycle = 'asserted' WHERE id = 'h'")
        await conn.execute("DELETE FROM memory_version WHERE id = 'h'")
    status = await runner.status()
    assert (status.state, status.version) == ("pending", M6 - 1)
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(1,)]
        async with conn.transaction():
            await insert_version(conn, "h", "claim", "inferred")
        with pytest.raises(RaiseException, match="lifecycle_inferred_to_asserted_unrepresentable"):
            await conn.execute("UPDATE memory_version SET lifecycle = 'asserted' WHERE id = 'h'")
