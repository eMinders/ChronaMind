"""E-016: the lifecycle transition table is closed and enforced by PostgreSQL for every writer (ARCHITECTURE_V2 2.3).

The oracle is ``model.axes`` (``TRANSITIONS`` and the kind-conditioned ``LEGAL_LIFECYCLE``): the database must accept
exactly what the model accepts, for all four kinds and all 5 x 5 ``(from, to)`` pairs.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, InvalidParameterValue, RaiseException

from custom_memory_engine.engine.deletion import ARCHIVAL_GUARD_TRIGGERS
from custom_memory_engine.engine.ingest import apply_candidate, resolve_held
from custom_memory_engine.engine.predicates import (
    audit_as_of,
    current_state,
    fetch_versions,
)
from custom_memory_engine.engine.transactions import end_multivalued, revise_transition_boundary
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
from tests.integration.rollbacks import adapt, has_column, rollback_sql
from tests.integration.test_migration_m1 import (
    APRIL,
    JULY,
    JUNE,
    START,
    candidate,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
    seed_turn,
)

pytestmark = pytest.mark.integration
# The lifecycle guard's own migration version (E-016; the plan's `M6`, shipped as migration 7).
GUARD_VERSION = 7
# The entry records of the same task (the creation rows of ARCHITECTURE_V2 2.3), shipped as migration 14.
ENTRY_VERSION = 14
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
        await adapt(
            conn,
            "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
            "content, event_range, recorded_range, layer, kind, lifecycle, retention) "
            "VALUES (%s, %s, 'u1', %s, 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
            "CASE WHEN %s THEN tstzrange(clock_now(), NULL) END, %s, %s, %s, 'durable')",
        ),
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
            if await has_column(conn, "memory_version", "method_id"):  # I1': inferred needs a registered method
                await conn.execute(
                    "UPDATE memory_version SET method_id = COALESCE(method_id, 'm-v1-consolidation') WHERE id = %s",
                    (version_id,),
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
        await conn.execute("SELECT set_config('app.clock', '2026-09-01T00:00:00+00:00', false)")
        async with conn.transaction():  # a withdrawal closes recorded time (its record, ARCHITECTURE_V2 2.3)
            await conn.execute(
                "UPDATE memory_version SET lifecycle = 'retired', recorded_range = tstzrange(lower(recorded_range), "
                "clock_now()) WHERE id = 'old'"
            )
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
                    "single_valued, content, event_range, recorded_range, layer, kind, lifecycle, retention) "
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
                        "single_valued, content, event_range, recorded_range, layer, kind, lifecycle, retention) "
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


async def test_rollback_restores_version_6_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=GUARD_VERSION - 1)),
            prepare=False,
        )
        recorded = await rows(
            conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (GUARD_VERSION - 1,)
        )
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical to the pre-guard schema
        # with the guard gone, the very transition the guard forbids is representable again
        await insert_version(conn, "h", "claim", "inferred")
        await conn.execute("UPDATE memory_version SET lifecycle = 'asserted' WHERE id = 'h'")
        await conn.execute("DELETE FROM memory_version WHERE id = 'h'")
    status = await runner.status()
    assert (status.state, status.version) == ("pending", GUARD_VERSION - 1)
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(1,)]
        async with conn.transaction():
            await insert_version(conn, "h", "claim", "inferred")
        with pytest.raises(RaiseException, match="lifecycle_inferred_to_asserted_unrepresentable"):
            await conn.execute("UPDATE memory_version SET lifecycle = 'asserted' WHERE id = 'h'")


# --------------------------------------------------- decision (a): does I10 apply to an explicit withdrawal?


async def test_i10_holds_for_explicit_withdrawal_because_the_withdrawal_is_itself_the_recorded_close(database) -> None:  # noqa: F811
    """Issue #67 decision (a): `asserted -> retired` is required to satisfy `(S)∨(C)`, and does so by construction.

    I10 forbids retirement that *silently* removes an answerable fact. An explicit withdrawal closes the belief
    interval, which is exactly branch `(S)`, so the precondition cannot fail for a real withdrawal: the only way to
    attempt the transition without `(S)` is to leave recorded time open, and that is a silent removal — which the
    guard refuses. `I10` therefore applies to explicit withdrawal *and* costs nothing: the guarantee and the
    transition are the same act. The fact stays answerable to `audit_as_of` afterwards (I10's requirement), and is
    gone from the ordinary channels (the point of withdrawing it).
    """
    _, schema = database
    async with connect(database) as conn:
        await seed_source(conn)
        async with conn.transaction():
            await insert_version(conn, "withdrawn", "claim", "asserted")
        # (1) A retirement that leaves recorded time open is a SILENT removal: (S) does not hold, (C) does not either.
        with pytest.raises(InvalidParameterValue, match="archival_precondition_unmet"):
            await conn.execute(
                "UPDATE memory_version SET status = 'archived', archival_branch = 'S' WHERE id = 'withdrawn'"
            )
        assert await rows(conn, "SELECT status, lifecycle FROM memory_version WHERE id = 'withdrawn'") == [
            ("active", "asserted")
        ]
        # (2) The explicit withdrawal closes recorded time, which IS branch (S): the transition is permitted...
        await conn.execute("SELECT set_config('app.clock', %s, false)", ("2026-09-01T00:00:00+00:00",))
        await conn.execute(
            "UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) "
            "WHERE id = 'withdrawn'"
        )
        await conn.execute(
            "UPDATE memory_version SET status = 'archived', archival_branch = 'S' WHERE id = 'withdrawn'"
        )
        assert await rows(conn, "SELECT status, lifecycle FROM memory_version WHERE id = 'withdrawn'") == [
            ("archived", "retired")
        ]
        # (3) ...and I10 holds: the withdrawn fact is still answerable at a belief time before the withdrawal.
        # The rows were first believed at the fixture clock (July); the withdrawal closed that interval in September.
        before = await fetch_versions(
            conn,
            audit_as_of("u1", START, datetime(2026, 8, 1, tzinfo=UTC)),
            schema_name=schema,
        )
        assert {version.id for version in before.versions} == {"withdrawn", "src"}
        # ...while the ordinary channels no longer return it, which is what withdrawing it was for.
        now = await fetch_versions(conn, current_state("u1", datetime(2026, 9, 2, tzinfo=UTC)), schema_name=schema)
        assert "withdrawn" not in {version.id for version in now.versions}


# ------------------------------------------- the records of entry into the lifecycle (migration 14, V2-D61)

AUGUST = datetime(2026, 8, 1, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 1, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 1, tzinfo=UTC)
PROMOTE = "UPDATE memory_version SET status = 'active', recorded_range = tstzrange(clock_now(), NULL) WHERE id = %s"


@asynccontextmanager
async def application_role(database, *, clock: datetime = JULY) -> AsyncIterator[AsyncConnection]:  # noqa: F811
    """A connection running as a freshly provisioned runtime role: not a superuser, subject to row security."""
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    try:
        async with connect(database) as admin:
            await provision_runtime_role(admin, schema, role)
        async with connect(database, clock=clock) as app:
            await app.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            assert await rows(app, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user") == [(False,)]
            yield app
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def decide(conn: AsyncConnection, version_id: str, decision: str) -> None:
    """One gate decision for ``version_id``, as ``engine.gate.record_decision`` writes it."""
    await conn.execute(
        "INSERT INTO memory_conflict (id, candidate_version_id, user_id, relation, decision, reason_code, decided_by, "
        "decided_at) VALUES (%s, %s, 'u1', 'unrelated', %s, 'gate_passed', 'validation-gate', clock_now())",
        ("mc-" + uuid4().hex, version_id, decision),
    )


async def enter(
    conn: AsyncConnection, version_id: str, kind: str, lifecycle: str, *, decision: str | None = None
) -> None:
    """Create a row of ``lifecycle`` with its evidence/derivation records, plus ``decision`` if one is given."""
    status = STATUS_OF[lifecycle]
    async with conn.transaction():
        await _insert_version(conn, version_id, kind, lifecycle, status, status == "active")
        if decision is not None:
            await decide(conn, version_id, decision)


async def stage(
    conn: AsyncConnection, version_id: str, kind: str, status: str, *, decision: str | None = None
) -> None:
    """An unbelieved v1 row (staged `candidate`, or held `conflict_pending`) with the evidence a promotion needs."""
    lifecycle = "retired" if status == "candidate" else "contested"
    async with conn.transaction():
        await _insert_version(conn, version_id, kind, lifecycle, status, False)
        await conn.execute(
            "INSERT INTO memory_evidence VALUES (%s, 't0', 'u1', 'I am vegetarian', 'support')", (version_id,)
        )
        if decision is not None:
            await decide(conn, version_id, decision)


async def believe_claim(
    conn: AsyncConnection, version_id: str, key: str, content: str, lower: datetime, upper: datetime | None
) -> None:
    """A believed claim over ``[lower, upper)`` with evidence and no decision (the caller adds one or not)."""
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
        "content, event_range, recorded_range, layer) VALUES (%s, %s, 'u1', 'active', 'user', 'diet', false, %s, "
        "tstzrange(%s, %s), tstzrange(clock_now(), NULL), 'semantic')",
        (version_id, key, content, lower, upper),
    )
    await conn.execute(
        "INSERT INTO memory_evidence VALUES (%s, 't0', 'u1', 'I am vegetarian', 'support')", (version_id,)
    )


async def test_each_creation_needs_its_record_under_the_application_role(database) -> None:  # noqa: F811
    """ARCHITECTURE_V2 2.3, the creation rows: `asserted` needs an accept decision, `contested` a hold decision.

    Raw SQL as the runtime role, judged at commit: no decision, or the wrong decision, refuses the whole transaction.
    `observed` and `inferred` owe their evidence and derivation + method (migrations 7 and 10), not a decision.
    """
    async with connect(database) as admin:
        await seed_source(admin)
    async with application_role(database) as app:
        for kind, lifecycle, record in (
            ("claim", "asserted", "accept"),
            ("procedure", "asserted", "accept"),
            ("claim", "contested", "hold"),
            ("abstraction", "contested", "hold"),
        ):
            with pytest.raises(RaiseException, match="lifecycle_requires_gate_decision"):
                await enter(app, "bare", kind, lifecycle)
            for wrong in {"accept", "hold", "reject"} - {record}:
                with pytest.raises(RaiseException, match="lifecycle_requires_gate_decision"):
                    await enter(app, "wrong", kind, lifecycle, decision=wrong)
            await enter(app, f"{kind}-{lifecycle}", kind, lifecycle, decision=record)
        await enter(app, "episode", "episode", "observed")
        await enter(app, "inference", "claim", "inferred")
        assert await rows(app, "SELECT count(*) FROM memory_version WHERE id IN ('bare', 'wrong')") == [(0,)]
        assert await rows(app, "SELECT id, lifecycle FROM memory_version WHERE id <> 'src' ORDER BY id") == [
            ("abstraction-contested", "contested"),
            ("claim-asserted", "asserted"),
            ("claim-contested", "contested"),
            ("episode", "observed"),
            ("inference", "inferred"),
            ("procedure-asserted", "asserted"),
        ]


async def test_a_staged_or_held_candidate_enters_the_lifecycle_only_with_its_adjudication(database) -> None:  # noqa: F811
    """The two v1 ways into the lifecycle that are not creations cannot route around the creation records."""
    async with connect(database) as admin:
        await seed_source(admin)
    async with application_role(database) as app:
        # a staged candidate (`candidate`, retired) promoted to a belief: the R4 promotion is an adjudication
        await stage(app, "staged", "claim", "candidate")
        with pytest.raises(RaiseException, match="lifecycle_requires_adjudication_record"):
            async with app.transaction():
                await app.execute(PROMOTE, ("staged",))
        async with app.transaction():
            await app.execute(PROMOTE, ("staged",))
            await decide(app, "staged", "accept")
        # a held v1 persona accepted as an inference: an adjudication AND a derivation, like any other inference
        await stage(app, "persona", "abstraction", "conflict_pending", decision="hold")
        with pytest.raises(RaiseException, match="inferred_requires_derivation_record"):
            async with app.transaction():
                await app.execute(PROMOTE, ("persona",))
                await decide(app, "persona", "accept")
        with pytest.raises(RaiseException, match="lifecycle_requires_adjudication_record"):
            async with app.transaction():
                await app.execute("INSERT INTO memory_derivation VALUES ('persona', 'src', 'u1', 'consolidation')")
                await app.execute(PROMOTE, ("persona",))
        async with app.transaction():
            await app.execute("INSERT INTO memory_derivation VALUES ('persona', 'src', 'u1', 'consolidation')")
            await app.execute(PROMOTE, ("persona",))
            await decide(app, "persona", "accept")
        # and the application role cannot create a persona inference without a derivation either: the v1 exemption
        # covers existing persona rows and the operator's harness, not new writes (V2-D61)
        with pytest.raises(RaiseException, match="inferred_requires_derivation_record"):
            async with app.transaction():
                await app.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, recorded_range, layer) VALUES ('bare-persona', 'p', 'u1', "
                    "'active', 'user', 'diet', false, 'x', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), "
                    "'persona')",
                    (START,),
                )
                await app.execute("INSERT INTO memory_evidence VALUES ('bare-persona', 't0', 'u1', 'x', 'support')")
        assert await rows(app, "SELECT id, kind, lifecycle FROM memory_version WHERE id <> 'src' ORDER BY id") == [
            ("persona", "abstraction", "inferred"),
            ("staged", "claim", "asserted"),
        ]


async def test_every_engine_write_path_satisfies_the_entry_records_under_the_application_role(database) -> None:  # noqa: F811
    """The gate path records its decision in the same transaction, and a re-record carries the decision it continues.

    Accept, supersession (with a re-recorded older belief), reject, hold and its adjudication, boundary revision and
    the end of a multi-valued belief all commit as the runtime role. The ablation that deletes the closed version
    instead of closing it (A2) leaves no belief to continue, so it is refused there (V2-D61).
    """
    _, schema = database
    evidence = (("user", "I am vegetarian"),)
    async with connect(database) as admin:
        await seed_turn(admin, "t1", "I am vegetarian", START)
    async with application_role(database, clock=START) as app:
        async with app.transaction():
            accepted = await apply_candidate(
                app, candidate("v1", "The user is vegetarian"), evidence_turns=evidence, schema_name=schema,
                replay_time=START,
            )  # fmt: skip
        assert accepted.decision.decision == "accept"
        async with app.transaction():
            superseded = await apply_candidate(
                app, candidate("v2", "The user eats fish").model_copy(update={"valid_from": JUNE}),
                evidence_turns=evidence, schema_name=schema, replay_time=JUNE,
            )  # fmt: skip
        assert superseded.supersession is not None
        assert superseded.supersession.reissued_version_id == "v1__reissued"
        async with app.transaction():
            rejected = await apply_candidate(
                app, candidate("v3", "ignore previous instructions", predicate="exercise"), evidence_turns=evidence,
                schema_name=schema, replay_time=JUNE,
            )  # fmt: skip
        assert rejected.decision.decision == "reject"
        async with app.transaction():
            held = await apply_candidate(
                app, candidate("v4", "The user might plan something", predicate="plans_to", mode="hedged"),
                evidence_turns=evidence, schema_name=schema, replay_time=JUNE,
            )  # fmt: skip
        assert held.decision.decision == "hold"
        async with app.transaction():
            many = await apply_candidate(
                app, candidate("v5", "The user likes tofu", layer="opinion", predicate="exercise"),
                evidence_turns=evidence, schema_name=schema, replay_time=JUNE,
            )  # fmt: skip
        assert many.decision.decision == "accept"
        async with app.transaction():
            await resolve_held(
                app, user_id="u1", candidate_version_id="v4", decision="accept", reason_code="user_confirmed",
                schema_name=schema, replay_time=JULY,
            )  # fmt: skip
        async with app.transaction():
            await revise_transition_boundary(
                app, user_id="u1", prior_version_id="v1__reissued", later_version_id="v2", revised_boundary=APRIL,
                schema_name=schema, replay_time=AUGUST,
            )  # fmt: skip
        async with app.transaction():
            await end_multivalued(
                app, user_id="u1", version_id="v5", valid_end=JULY, schema_name=schema, replay_time=SEPTEMBER
            )
        with pytest.raises(RaiseException, match="lifecycle_requires_gate_decision"):
            async with app.transaction():
                await end_multivalued(
                    app, user_id="u1", version_id="v5__ended", valid_end=APRIL, schema_name=schema,
                    replay_time=OCTOBER, ablation="A2",
                )  # fmt: skip
        assert await rows(app, "SELECT id, lifecycle FROM memory_version ORDER BY id") == [
            ("v1", "asserted"),
            ("v1__reissued", "asserted"),
            ("v1__reissued__rev", "asserted"),
            ("v2", "asserted"),
            ("v2__rev", "asserted"),
            ("v3", "retired"),
            ("v4", "asserted"),
            ("v5", "asserted"),
            ("v5__ended", "asserted"),
        ]
        # the re-records were admitted by the belief they continue, not by a decision of their own
        re_records = ("v1__reissued", "v1__reissued__rev", "v2__rev", "v5__ended")
        assert await rows(
            app, "SELECT count(*) FROM memory_conflict WHERE candidate_version_id = ANY(%s)", (list(re_records),)
        ) == [(0,)]


async def test_a_re_record_may_claim_neither_new_event_time_nor_new_content(database) -> None:  # noqa: F811
    """Closing a belief licenses no free insert: only a copy within what was believed passes without a decision."""
    async with connect(database) as admin:
        await seed_source(admin)
    async with application_role(database) as app:
        async with app.transaction():
            await believe_claim(app, "b", "b-key", "x", START, JUNE)
            await decide(app, "b", "accept")
        await app.execute("SELECT set_config('app.clock', %s, false)", (AUGUST.isoformat(),))
        close = (
            "UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) WHERE id = 'b'"
        )
        for version_id, content, upper in (("longer", "x", None), ("other", "y", APRIL)):
            with pytest.raises(RaiseException, match="lifecycle_requires_gate_decision"):
                async with app.transaction():
                    await app.execute(close)
                    await believe_claim(app, version_id, "b-key", content, START, upper)
        with pytest.raises(
            RaiseException, match="lifecycle_requires_gate_decision"
        ):  # nothing closed: not a re-record
            async with app.transaction():
                await believe_claim(app, "copy", "b-key", "x", START, APRIL)
        async with app.transaction():
            await app.execute(close)
            await believe_claim(app, "shorter", "b-key", "x", START, APRIL)
        assert await rows(app, "SELECT id FROM memory_version WHERE memory_key = 'b-key' ORDER BY id") == [
            ("b",),
            ("shorter",),
        ]


async def test_a_superuser_is_not_bound_by_the_entry_records(database) -> None:  # noqa: F811
    """V2-D61: the operator's harness commits claims without the gate, as v1's own tests and evaluation do."""
    async with connect(database) as conn:
        await seed_source(conn)
        assert await rows(conn, "SELECT lifecycle FROM memory_version WHERE id = 'src'") == [("asserted",)]
        assert await rows(conn, "SELECT count(*) FROM memory_conflict") == [(0,)]


async def test_the_entry_record_triggers_are_deferrable_and_listed_for_deletion(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        for name in ("trg_memory_version_record_of_creation", "trg_memory_version_record_of_entry"):
            assert name in ARCHIVAL_GUARD_TRIGGERS  # r10: deletion judges the final state
            assert await rows(
                conn,
                "SELECT tgdeferrable, tginitdeferred FROM pg_trigger "
                "WHERE tgname = %s AND NOT tgisinternal AND tgrelid = 'memory_version'::regclass",
                (name,),
            ) == [(True, True)]


async def test_rollback_restores_version_13_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=ENTRY_VERSION - 1)),
            prepare=False,
        )
        recorded = await rows(
            conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (ENTRY_VERSION - 1,)
        )
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical to the version-13 schema
    status = await runner.status()
    assert (status.state, status.version) == ("pending", ENTRY_VERSION - 1)
    assert (await runner.apply()).action == "applied"
    async with application_role(database) as app:
        with pytest.raises(RaiseException, match="lifecycle_requires_gate_decision"):
            await enter(app, "bare", "claim", "asserted")
        assert await rows(app, "SELECT id FROM memory_version") == [("src",)]
