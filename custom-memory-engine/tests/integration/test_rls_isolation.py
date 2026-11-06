"""Cross-user isolation under a real restricted role (RL-071, Invariant 7).

Every other integration suite connects as the disposable database's superuser, which bypasses RLS, so a
green suite proves nothing about isolation. These tests connect through the restricted runtime role that
``security.provisioning`` creates and prove that cross-user reads and writes are blocked.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.pq import TransactionStatus

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.ingest import TurnInput, apply_candidate, persist_turn
from custom_memory_engine.engine.predicates import current_state, fetch_versions
from custom_memory_engine.engine.retrieval import rank_versions
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput, commit_initial_version
from custom_memory_engine.security import (
    USER_TABLES,
    RlsProvisioningError,
    TenantScopeError,
    privilege_violations,
    provision_runtime_role,
    tenant_scope,
    verify_forced_rls,
    verify_runtime_role,
)
from evaluation.stats.clopper_pearson import cp_upper
from research.benchmarks.own_I.generator import generate_suite_i
from tests.integration.schema import engine_database

pytestmark = pytest.mark.integration

MAR10 = datetime(2026, 3, 10, tzinfo=UTC)
NOW = datetime(2026, 10, 1, tzinfo=UTC)
MODEL = "BAAI/bge-small-en-v1.5"


def embedding(*weights: float) -> list[float]:
    base = [0.0] * 384
    for index, weight in enumerate(weights):
        base[index] = weight
    return base


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str, str]]:
    role = "cme_rt_" + uuid4().hex[:12]
    async with engine_database() as (config, schema):
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            await provision_runtime_role(conn, schema, role)
        try:
            yield config, schema, role
        finally:
            async with await AsyncConnection.connect(
                config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
            ) as conn:
                await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str, str]) -> AsyncIterator[AsyncConnection]:
    """Superuser connection used for seeding and controls (it bypasses RLS on purpose)."""
    config, schema, _ = database
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET timezone = 'UTC'")
        yield conn


async def seed_user(
    conn: AsyncConnection, schema: str, user: str, content: str, *, with_vector: tuple[float, ...] | None = None
) -> None:
    """Insert one user with a session, a turn and one active memory version holding ``content``."""
    await conn.execute("SELECT set_config('app.user_id', %s, false)", (user,))
    await conn.execute(
        "INSERT INTO predicate_schema (predicate_key, cardinality, default_layer) "
        "VALUES ('fact', 'multi', 'semantic') ON CONFLICT DO NOTHING"
    )
    await conn.execute(
        "INSERT INTO app_user (id, external_ref, created_at) VALUES (%s, %s, %s)", (user, f"ext-{user}", MAR10)
    )
    await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", (f"s-{user}", user, MAR10))
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, %s, %s, 1, 'user', %s, %s)",
        (f"t-{user}", f"s-{user}", user, content, MAR10),
    )
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await commit_initial_version(
            conn,
            CandidateVersionInput(
                version_id=f"v-{user}",
                memory_key=f"{user}:fact",
                user_id=user,
                subject_key="user",
                predicate_key="fact",
                single_valued=False,
                content=content,
                valid_from=MAR10,
                evidence=(EvidenceInput(turn_id=f"t-{user}", evidence_span=content),),
            ),
            schema_name=schema,
            replay_time=MAR10,
        )
        if with_vector is not None:
            await conn.execute(
                "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding) "
                "VALUES (%s, %s, %s, %s)",
                (f"v-{user}", MODEL, user, embedding(*with_vector)),
            )
    await conn.execute("SELECT set_config('app.user_id', '', false)")


@pytest.mark.asyncio
async def test_provisioned_role_is_restricted_and_every_user_table_forces_rls(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    _, schema, role = database
    async with connect(database) as conn:
        await verify_forced_rls(conn, schema)
        await verify_runtime_role(conn, role)
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = ANY(%s) "
                "ORDER BY c.relname",
                (schema, list(USER_TABLES)),
            )
            rows = await cursor.fetchall()
    # Recorded for the PR evidence: one line per table, all true/true.
    print("FORCE ROW LEVEL SECURITY verification:", *[f"{n}={e and f}" for n, e, f in rows])
    assert len(rows) == len(USER_TABLES)
    assert all(enabled and forced for _, enabled, forced in rows)


@pytest.mark.asyncio
async def test_verification_fails_when_force_is_removed_or_the_role_can_bypass_rls(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    _, schema, _ = database
    async with connect(database) as conn:
        async with conn.transaction(force_rollback=True):
            await conn.execute("ALTER TABLE memory_version NO FORCE ROW LEVEL SECURITY")
            with pytest.raises(RlsProvisioningError, match="row_level_security_not_forced"):
                await verify_forced_rls(conn, schema)
        bypass = "cme_bypass_" + uuid4().hex[:10]
        await conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN BYPASSRLS").format(sql.Identifier(bypass)))
        try:
            with pytest.raises(RlsProvisioningError, match="runtime_role_has_dangerous_attribute"):
                await verify_runtime_role(conn, bypass)
        finally:
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(bypass)))
        with pytest.raises(RlsProvisioningError, match="runtime_role_missing"):
            await verify_runtime_role(conn, "cme_no_such_role")


@pytest.mark.asyncio
async def test_same_user_reads_succeed_and_other_tenants_are_invisible(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    _, schema, role = database
    async with connect(database) as conn:
        await seed_user(conn, schema, "u1", "u1 keeps a green notebook")
        await seed_user(conn, schema, "u2", "u2 keeps a red notebook")
        await seed_user(conn, schema, "u3", "u3 keeps a blue notebook")
        # A grant between two *other* tenants: u1 must not even learn that it exists.
        await conn.execute(
            "INSERT INTO scope_grant (id, owner_user_id, principal_id, scope_path, granted_at) "
            "VALUES ('g-u2-u3', 'u2', 'u3', '/u/u2/', %s)",
            (MAR10,),
        )
        async with tenant_scope(conn, "u1", role=role):
            observed: dict[str, int] = {}
            for table in USER_TABLES:
                async with conn.cursor() as cursor:
                    await cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                    observed[table] = (await cursor.fetchone() or (-1,))[0]
        # Control, from a connection that bypasses RLS: the restricted role saw exactly the caller's rows,
        # and for every table that holds both tenants' data it saw strictly fewer rows than exist.
        for table in USER_TABLES:
            if table == "scope_grant":
                visible_to_u1 = sql.SQL("'u1' IN (owner_user_id, principal_id)")
            else:
                visible_to_u1 = sql.SQL("{} = 'u1'").format(sql.Identifier("id" if table == "app_user" else "user_id"))
            async with conn.cursor() as cursor:
                await cursor.execute(
                    sql.SQL("SELECT count(*) FILTER (WHERE {cond}), count(*) FROM {tbl}").format(
                        cond=visible_to_u1, tbl=sql.Identifier(table)
                    )
                )
                own, total = await cursor.fetchone() or (-1, -1)
            assert observed[table] == own, f"{table}: restricted role saw {observed[table]}, caller owns {own}"
            if table in {"app_user", "session", "turn", "memory_version", "memory_evidence", "audit_event"}:
                assert 0 < own < total, f"{table}: control must hold both tenants' rows"
        async with tenant_scope(conn, "u1", role=role):
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT id FROM memory_version WHERE id = 'v-u2'")
                assert await cursor.fetchall() == [], "an explicit id lookup of another tenant's row returns nothing"
                await cursor.execute("SELECT id FROM memory_version")
                assert await cursor.fetchall() == [("v-u1",)]


@pytest.mark.asyncio
async def test_cross_user_update_and_delete_touch_zero_rows_and_insert_is_rejected(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    _, schema, role = database
    async with connect(database) as conn:
        await seed_user(conn, schema, "u1", "u1 secret")
        await seed_user(conn, schema, "u2", "u2 secret")
        async with tenant_scope(conn, "u1", role=role):
            async with conn.cursor() as cursor:
                await cursor.execute("UPDATE memory_version SET status = 'rejected' WHERE user_id = 'u2'")
                assert cursor.rowcount == 0
                await cursor.execute("DELETE FROM memory_version WHERE user_id = 'u2'")
                assert cursor.rowcount == 0
        # Turns and sessions are append-only for the runtime (V2-D54): any rewrite is refused outright.
        for statement in (
            "UPDATE turn SET content = 'overwritten' WHERE user_id = 'u2'",
            "DELETE FROM turn WHERE user_id = 'u2'",
            "DELETE FROM session WHERE user_id = 'u2'",
        ):
            with pytest.raises(InsufficientPrivilege):
                async with tenant_scope(conn, "u1", role=role):
                    await conn.execute(cast(LiteralString, statement))
        with pytest.raises(InsufficientPrivilege):
            async with tenant_scope(conn, "u1", role=role):
                await conn.execute(
                    "INSERT INTO session (id, user_id, started_at) VALUES ('forged', 'u2', %s)", (MAR10,)
                )
        # Control: u2's rows are all still there, which only a bypassing connection can see.
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT content FROM turn WHERE user_id = 'u2'")
            assert await cursor.fetchall() == [("u2 secret",)]
            await cursor.execute("SELECT count(*) FROM session WHERE id = 'forged'")
            assert (await cursor.fetchone() or (None,))[0] == 0


@pytest.mark.asyncio
async def test_scope_exit_restores_the_role_and_clears_the_tenant(database: tuple[DatabaseConfig, str, str]) -> None:
    _, schema, role = database
    async with connect(database) as conn:
        await seed_user(conn, schema, "u1", "u1 note")
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT current_user")
            original = (await cursor.fetchone() or (None,))[0]
        async with tenant_scope(conn, "u1", role=role):
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT current_user, current_setting('app.user_id', true)")
                assert await cursor.fetchone() == (role, "u1")
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT current_user, NULLIF(current_setting('app.user_id', true), '')")
            assert await cursor.fetchone() == (original, None)
        with pytest.raises(RuntimeError, match="boom"):
            async with tenant_scope(conn, "u1", role=role):
                raise RuntimeError("boom")
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT current_user")
            assert (await cursor.fetchone() or (None,))[0] == original


@pytest.mark.asyncio
async def test_mismatched_scope_is_rejected_before_any_statement_and_nesting_is_refused(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    _, _, role = database
    async with connect(database) as conn:
        with pytest.raises(InsufficientPrivilege, match="tenant_scope_mismatch"):
            async with tenant_scope(conn, "u1", role=role, requested_user_id="u2"):
                pytest.fail("block_must_not_run")
        assert conn.info.transaction_status == TransactionStatus.IDLE
        async with conn.transaction():
            with pytest.raises(TenantScopeError, match="idle_connection_required"):
                async with tenant_scope(conn, "u1", role=role):
                    pytest.fail("block_must_not_run")


@pytest.mark.asyncio
async def test_hybrid_retrieval_and_temporal_reads_never_return_another_tenants_rows(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    _, schema, role = database
    async with connect(database) as conn:
        await seed_user(conn, schema, "u1", "the notebook is green", with_vector=(1.0,))
        await seed_user(conn, schema, "u2", "the notebook is red", with_vector=(1.0,))
        for user, other in (("u1", "u2"), ("u2", "u1")):
            async with tenant_scope(conn, user, role=role):
                temporal = await fetch_versions(conn, current_state(user, NOW), schema_name=schema)
                assert [v.id for v in temporal.versions] == [f"v-{user}"]
                for fusion in ("rrf", "dense", "lexical"):
                    outcome = await rank_versions(
                        conn,
                        current_state(user, NOW),
                        query_text="notebook",
                        query_embedding=embedding(1.0),
                        fusion=fusion,  # type: ignore[arg-type]
                        schema_name=schema,
                    )
                    ids = [r.version.id for r in outcome.ranked]
                    assert ids == [f"v-{user}"], f"{fusion} leaked: {ids}"
                    assert f"v-{other}" not in ids
        # Asking for another tenant explicitly fails closed instead of reading around the policy.
        async with tenant_scope(conn, "u1", role=role):
            with pytest.raises(InsufficientPrivilege):
                await fetch_versions(conn, current_state("u2", NOW), schema_name=schema)


@pytest.mark.asyncio
async def test_suite_i_dev_probes_report_zero_cross_user_exposure_under_the_restricted_role(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    _, schema, role = database
    dataset = generate_suite_i("dev")
    users = {s.user_a_id: s.user_a_canary for s in dataset.scenarios} | {
        s.user_b_id: s.user_b_canary for s in dataset.scenarios
    }
    async with connect(database) as conn:
        for user, canary in sorted(users.items()):
            await seed_user(conn, schema, user, f"my reference is {canary}")

        exposures = 0
        rejections = 0
        own_visible = 0
        probes = 0
        exposed_scenarios = 0
        for scenario in dataset.scenarios:
            before = exposures
            for probe in scenario.probes:
                probes += 1
                requested = probe.requested_scope_user_id
                if probe.expect_scope_rejection:
                    with pytest.raises(InsufficientPrivilege, match="tenant_scope_mismatch"):
                        async with tenant_scope(
                            conn, probe.authenticated_user_id, role=role, requested_user_id=requested
                        ):
                            pytest.fail("block_must_not_run")
                    rejections += 1
                    continue
                async with tenant_scope(conn, probe.authenticated_user_id, role=role):
                    outcome = await rank_versions(
                        conn,
                        current_state(probe.authenticated_user_id, NOW),
                        query_text=probe.query_text,
                        fusion="lexical",
                        schema_name=schema,
                    )
                    async with conn.cursor() as cursor:
                        await cursor.execute(
                            "SELECT count(*) FROM memory_version WHERE content LIKE %s",
                            (f"%{probe.forbidden_other_user_canary}%",),
                        )
                        direct = (await cursor.fetchone() or (0,))[0]
                        await cursor.execute(
                            "SELECT count(*) FROM turn WHERE content LIKE %s",
                            (f"%{probe.forbidden_other_user_canary}%",),
                        )
                        direct += (await cursor.fetchone() or (0,))[0]
                    rendered = " ".join(r.version.content for r in outcome.ranked)
                    exposures += direct + rendered.count(probe.forbidden_other_user_canary)
                    if probe.expected_own_canary:
                        async with conn.cursor() as cursor:
                            await cursor.execute(
                                "SELECT count(*) FROM memory_version WHERE content LIKE %s",
                                (f"%{probe.expected_own_canary}%",),
                            )
                            own_visible += (await cursor.fetchone() or (0,))[0]
            exposed_scenarios += int(exposures > before)
        scenarios = len(dataset.scenarios)
        bound = cp_upper(exposed_scenarios, scenarios)  # one-sided 97.5% (STATISTICS.md §4)
        print(
            f"Suite I dev: probes={probes} cross_user_exposures={exposures} scope_rejections={rejections} "
            f"exposed_scenarios={exposed_scenarios}/{scenarios} cp_upper_97_5={bound:.6f}"
        )
        assert exposed_scenarios == 0
        assert abs(bound - (1 - 0.025 ** (1 / scenarios))) < 1e-9
        assert probes == dataset.total_probes
        assert exposures == 0
        assert rejections > 0, "the suite includes scope-mismatch probes that must be rejected"
        assert own_visible > 0

        # Positive control: the same canaries ARE present for a connection that bypasses RLS, so the
        # zero above is a property of the restricted role and not of an empty database.
        sample = dataset.scenarios[0]
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT count(*) FROM memory_version WHERE content LIKE %s", (f"%{sample.user_b_canary}%",)
            )
            assert (await cursor.fetchone() or (0,))[0] == 1


@pytest.mark.asyncio
async def test_privilege_catalog_assertion_is_exact_and_detects_extra_or_missing_grants(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    """#47 acceptance: no privileges beyond §3.1 — asserted from the catalog, both directions."""
    _, schema, role = database
    async with connect(database) as conn:
        assert await privilege_violations(conn, schema, role) == []
        await conn.execute(sql.SQL("GRANT TRUNCATE ON memory_version TO {}").format(sql.Identifier(role)))
        await conn.execute(sql.SQL("REVOKE DELETE ON job FROM {}").format(sql.Identifier(role)))
        await conn.execute(
            sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role))
        )
        found = await privilege_violations(conn, schema, role)
    assert "extra_truncate_on_memory_version" in found
    assert "missing_delete_on_job" in found
    assert "extra_create_on_engine_schema" in found


@pytest.mark.asyncio
async def test_deletion_runs_under_the_restricted_role_and_cannot_cross_tenants(
    database: tuple[DatabaseConfig, str, str],
) -> None:
    """The real write and delete paths, including the gate's memory_conflict update, run as the role."""
    _, schema, role = database
    async with connect(database) as conn:
        await conn.execute(
            "INSERT INTO predicate_schema VALUES ('badge', 'single', 'semantic') ON CONFLICT DO NOTHING"
        )
        await seed_user(conn, schema, "u2", "u2 private BADGE-U2-SECRET")
        await conn.execute("SELECT set_config('app.user_id', '', false)")
        for index, text in enumerate(["my badge is AAA111", "my badge is now BBB222"]):
            moment = MAR10.replace(hour=index + 1)
            async with tenant_scope(conn, "u1", role=role):
                await persist_turn(
                    conn,
                    TurnInput(f"t{index}", "u1", "s-u1", index + 1, "user", text, moment),
                    schema_name=schema,
                    create_user=True,
                )
                await apply_candidate(
                    conn,
                    CandidateVersionInput(
                        version_id=f"u1-v{index}",
                        memory_key="badge",
                        user_id="u1",
                        subject_key="user",
                        predicate_key="badge",
                        content=text,
                        valid_from=moment,
                        evidence=(EvidenceInput(turn_id=f"t{index}", evidence_span=text, evidence_role="user"),),
                    ),
                    evidence_turns=(("user", text),),
                    schema_name=schema,
                    replay_time=moment,
                )
        async with tenant_scope(conn, "u1", role=role):
            receipt = await delete_memory(
                conn, "u1", scope="memory", target_ref="badge", schema_name=schema, replay_time=NOW
            )
        assert receipt.deleted_versions == 3
        with pytest.raises(InsufficientPrivilege):
            async with tenant_scope(conn, "u1", role=role):
                await delete_memory(conn, "u2", scope="all", schema_name=schema, replay_time=NOW)
        async with tenant_scope(conn, "u1", role=role):
            cursor = await conn.execute("SELECT count(*) FROM memory_version")
            assert (await cursor.fetchone() or (1,))[0] == 0  # u1 has nothing left, u2 is invisible
        cursor = await conn.execute("SELECT count(*) FROM memory_version WHERE user_id = 'u2'")
        assert (await cursor.fetchone() or (0,))[0] == 1  # superuser control: u2 untouched
