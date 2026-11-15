"""E-023 / G2: the conversational and coding profiles on **one** migrated database, isolated and at parity.

A deployment that serves two applications builds one engine per profile from the same code (``V2-D08``): the profile
document is the only difference. This module is the runtime half of that claim. It provisions one restricted runtime
role on one migrated schema, builds the two engines on the *same* runtime, and gives each profile its own tenant —
``carol`` in ``/u/carol/`` (conversational) and ``alice`` in ``/u/alice/p/chrona/`` (coding). It then asserts that:

* both profiles write to the same tables and read their own rows back through ``Engine.call``;
* neither profile can read the other's rows, and the restricted role cannot even learn they exist
  (``THREAT_MODEL.md`` T-11/T-18: absent and unreadable are the same answer), while a superuser control sees both;
* parity is checkable, not asserted by prose: one schema, one role, one migration set (``migrate.status`` is
  ``applied`` at the shipped version), the same engine wiring, and the profile document is the only difference.
  Source identity (no profile-keyed branch in ``src/``) is ``E-025``'s mechanical check and is referenced, not
  duplicated here.

**Order independence.** The two profile *suites* run as separate pytest processes against private clones, so their
order cannot matter. The only place where order could matter is interleaved tenancy in one schema, so every test here
is parametrised over which profile's workload runs first; both orders must produce the same stored state, the same
isolation and the same parity.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql

from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import Receipt, Runtime
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.profile import load_profile
from custom_memory_engine.schema.migrate import VERSION, MigrationConfig, MigrationRunner
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from evaluation.stats.clopper_pearson import cp_upper
from tests.integration.schema import engine_database
from tests.integration.test_migration_m1 import connect, rows

pytestmark = pytest.mark.integration

PROFILES = Path(__file__).resolve().parents[2] / "src" / "custom_memory_engine" / "profiles"
CONVERSATIONAL, CODING = "carol", "alice"
CONVERSATIONAL_SCOPE, CODING_SCOPE = f"/u/{CONVERSATIONAL}/", f"/u/{CODING}/p/chrona/"
CONVERSATIONAL_CANARY, CODING_CANARY = "CAROL-CANARY-7f3a", "ALICE-CANARY-9c1d"
CONVERSATIONAL_TURN = f"my dietary rule is {CONVERSATIONAL_CANARY}"
CODING_TURN = f"the focused test failed with {CODING_CANARY}"
CODING_TOOL_OUTPUT = f"pytest -q tests/test_check.py\nAssertionError: {CODING_CANARY}\n"
MAR10 = datetime(2026, 3, 10, tzinfo=UTC)
ORDERS = ("conversational-first", "coding-first")
#: One probe per direction: a coding-profile tenant reading the conversational tenant, and the reverse.
SCENARIOS = 2


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    async with engine_database() as (config, schema):
        yield config, schema


def caller(user: str, *, repo: str | None = None) -> CallerContext:
    """A caller whose read set is its personal root, plus the repository scope for the coding profile."""
    selectors = [Selector.of(f"/u/{user}/")] + ([Selector.of(repo)] if repo else [])
    return CallerContext.from_credential(
        Credential(user, "C", False), write_scope=f"/u/{user}/", read_set=resolve_read_set(user, selectors, [])
    )


def read_set_for(user: str) -> CallerContext:
    return caller(user, repo=CODING_SCOPE if user == CODING else None)


def source(*, kind: str, ref: str, span: str, content: str) -> dict[str, str]:
    return {"type": kind, "ref": ref, "span": span, "content_hash": canonical_hash(content)}


@asynccontextmanager
async def dual(database: tuple[DatabaseConfig, str]) -> AsyncIterator[tuple[AsyncConnection, Engine, Engine, str]]:
    """One schema, one restricted role, two engines: the profile document is the only difference."""
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(database) as conn:
        await conn.execute("SELECT set_config('app.clock', %s, false)", (MAR10.isoformat(),))
        for user, turn in ((CONVERSATIONAL, CONVERSATIONAL_TURN), (CODING, CODING_TURN)):
            await conn.execute(
                "INSERT INTO app_user (id, external_ref, created_at) VALUES (%s, %s, %s)",
                (user, f"ext-{user}", MAR10),
            )
            await conn.execute(
                "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", (f"s-{user}", user, MAR10)
            )
            await conn.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES (%s, %s, %s, 1, 'user', %s, %s)",
                (f"t-{user}", f"s-{user}", user, turn, MAR10),
            )
        await provision_runtime_role(conn, schema, role)
        runtime = Runtime(conn, role, schema)
        conversational, coding = build_engine(runtime), build_engine(runtime)
        register_ingest_handlers(conversational, runtime, load_profile(PROFILES / "conversational.json"))
        register_read_handlers(conversational, runtime)
        register_ingest_handlers(coding, runtime, load_profile(PROFILES / "coding.json"))
        register_read_handlers(coding, runtime)
        try:
            yield conn, conversational, coding, role
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def store_conversational(engine: Engine) -> str:
    """A claim grounded in the conversational tenant's own turn, through ``Engine.call``."""
    receipt = await engine.call(
        caller(CONVERSATIONAL),
        "assert",
        {
            "scope": CONVERSATIONAL_SCOPE,
            "kind": "claim",
            "content": CONVERSATIONAL_TURN,
            "evidence": [
                source(
                    kind="turn",
                    ref=f"t-{CONVERSATIONAL}",
                    span=CONVERSATIONAL_CANARY,
                    content=CONVERSATIONAL_TURN,
                )
            ],
            "event_range": None,
            "idempotency_key": "carol-claim",
        },
    )
    assert isinstance(receipt, Receipt) and receipt.effect == "asserted", receipt
    return receipt.id


async def store_coding(engine: Engine) -> str:
    """A tool-output episode under the repository scope, through ``Engine.call``."""
    receipt = await engine.call(
        caller(CODING, repo=CODING_SCOPE),
        "ingest_episode",
        {
            "scope": CODING_SCOPE,
            "source": source(kind="tool", ref="tool:pytest/failure", span=CODING_CANARY, content=CODING_TOOL_OUTPUT),
            "content": CODING_TOOL_OUTPUT,
            "event_time": MAR10.isoformat(),
            "idempotency_key": "alice-episode",
        },
    )
    assert isinstance(receipt, Receipt) and receipt.effect == "observed", receipt
    return receipt.id


async def store_both(conversational: Engine, coding: Engine, order: str) -> dict[str, str]:
    """Seed both profiles in ``order``; the idempotency keys are the same either way (the ids are minted per run)."""
    workloads = {
        "conversational-first": (
            (store_conversational, conversational, "conversational"),
            (store_coding, coding, "coding"),
        ),
        "coding-first": (
            (store_coding, coding, "coding"),
            (store_conversational, conversational, "conversational"),
        ),
    }[order]
    ids: dict[str, str] = {}
    for store, engine, name in workloads:
        ids[name] = await store(engine)
    return ids


async def schema_status(database: tuple[DatabaseConfig, str]) -> tuple[str, int | None]:
    """``migrate.status`` on the schema both profiles are running against (the parity command)."""
    config, schema = database
    status = await MigrationRunner(MigrationConfig(database_url=config.database_url, schema_name=schema)).status()
    return status.state, status.version


@pytest.mark.parametrize("order", ORDERS)
async def test_both_profiles_store_and_read_in_one_migrated_schema_at_parity(
    database: tuple[DatabaseConfig, str], order: str
) -> None:
    """One schema, one role, one migration set; the profile document is the only difference."""
    async with dual(database) as (conn, conversational, coding, role):
        _, schema = database
        ids = await store_both(conversational, coding, order)
        state, version = await schema_status(database)
        conv_runtime = conversational.resolver.runtime  # type: ignore[attr-defined]
        coding_runtime = coding.resolver.runtime  # type: ignore[attr-defined]
        # One runtime, one role, one schema: the fixture contract the parity claim rests on, so a second runtime
        # could not be introduced without failing here. The profile document is the only difference between them.
        assert conv_runtime is coding_runtime
        assert (conv_runtime.role, conv_runtime.schema_name) == (role, schema)
        assert (state, version) == ("applied", VERSION)
        print(
            f"dual-profile parity ({order}): schema={conv_runtime.schema_name} role={role} "
            f"migration_state={state} version={version}"
        )

        for user, engine, artifact_id in (
            (CONVERSATIONAL, conversational, ids["conversational"]),
            (CODING, coding, ids["coding"]),
        ):
            scope = CONVERSATIONAL_SCOPE if user == CONVERSATIONAL else CODING_SCOPE
            got = await engine.call(read_set_for(user), "get", {"artifact_id": artifact_id})
            assert isinstance(got, Artifact) and (got.scope_path, got.channel) == (scope, "facts"), user
            found = await engine.call(read_set_for(user), "query", {"request": {"scope": scope}})
            assert isinstance(found, QueryResult) and [a.artifact_id for a in found.facts] == [artifact_id], user

        # Both profiles write the same table: two tenants, two rows, one schema.
        assert await rows(conn, "SELECT user_id, scope_path FROM memory_version ORDER BY user_id") == [
            (CODING, CODING_SCOPE),
            (CONVERSATIONAL, CONVERSATIONAL_SCOPE),
        ]


@pytest.mark.parametrize("order", ORDERS)
async def test_neither_profile_reads_the_other_through_engine_call(
    database: tuple[DatabaseConfig, str], order: str
) -> None:
    """Cross-profile reads answer ``forbidden`` in both directions, whichever profile ran first."""
    async with dual(database) as (_conn, conversational, coding, _role):
        ids = await store_both(conversational, coding, order)
        for reader, engine, other_key in (
            (CODING, coding, "conversational"),
            (CONVERSATIONAL, conversational, "coding"),
        ):
            with pytest.raises(EntryError) as refused:
                await engine.call(read_set_for(reader), "get", {"artifact_id": ids[other_key]})
            assert refused.value.code == "forbidden"
        # And a scoped query never returns the other profile's artifact.
        for user, engine in ((CONVERSATIONAL, conversational), (CODING, coding)):
            scope = CONVERSATIONAL_SCOPE if user == CONVERSATIONAL else CODING_SCOPE
            found = await engine.call(read_set_for(user), "query", {"request": {"scope": scope}})
            assert isinstance(found, QueryResult)
            returned = {
                a.artifact_id
                for bucket in (found.facts, found.hypotheses, found.procedures, found.conflicts)
                for a in bucket
            }
            assert returned == {ids["conversational" if user == CONVERSATIONAL else "coding"]}


@pytest.mark.parametrize("order", ORDERS)
async def test_the_restricted_role_makes_the_other_profile_indistinguishable(
    database: tuple[DatabaseConfig, str], order: str
) -> None:
    """``THREAT_MODEL.md`` T-11/T-18: the other tenant's rows are absent, not merely filtered.

    Each direction also asserts the reader still sees its own row, so a policy that hid every row could not score 0.
    The positive control runs on the seeding connection, which bypasses RLS: both canaries exist, so the zero is a
    property of the restricted role and not of an empty database.
    """
    async with dual(database) as (conn, conversational, coding, role):
        ids = await store_both(conversational, coding, order)
        exposures = 0
        exposed = 0
        for reader, other, other_key, canary in (
            (CODING, CONVERSATIONAL, "conversational", CONVERSATIONAL_CANARY),
            (CONVERSATIONAL, CODING, "coding", CODING_CANARY),
        ):
            before = exposures
            async with tenant_scope(conn, reader, role=role):
                assert await rows(conn, "SELECT id FROM memory_version WHERE user_id = %s", (other,)) == []
                assert await rows(conn, "SELECT id FROM memory_version WHERE content LIKE %s", (f"%{canary}%",)) == []
                assert await rows(conn, "SELECT id FROM memory_version WHERE id = %s", (ids[other_key],)) == []
                leaked = await rows(conn, "SELECT id FROM memory_version WHERE user_id <> %s", (reader,))
                exposures += len(leaked)
                own = await rows(conn, "SELECT id FROM memory_version WHERE user_id = %s", (reader,))
            assert own, f"{reader} must still see its own row through the restricted role"
            exposed += int(exposures > before)
        # Positive control: the seeding connection sees exactly one row per canary.
        assert await rows(
            conn, "SELECT count(*) FROM memory_version WHERE content LIKE %s", (f"%{CONVERSATIONAL_CANARY}%",)
        ) == [(1,)]
        assert await rows(
            conn, "SELECT count(*) FROM memory_version WHERE content LIKE %s", (f"%{CODING_CANARY}%",)
        ) == [(1,)]
        assert exposures == 0, f"cross-profile rows visible through the restricted role: {exposures}"
        bound = cp_upper(exposed, SCENARIOS)  # one-sided 97.5% (STATISTICS.md section 4.2)
        print(
            f"dual-profile isolation ({order}): cross_profile_exposures={exposures} "
            f"exposed_scenarios={exposed}/{SCENARIOS} cp_upper_97_5={bound:.6f}"
        )
        assert abs(bound - (1 - 0.025 ** (1 / SCENARIOS))) < 1e-9
