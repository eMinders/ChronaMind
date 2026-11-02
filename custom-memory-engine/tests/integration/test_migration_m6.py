"""E-014 / M6 and the record half of E-016: structural tables, the method reference, transition records."""

from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.errors import (
    CheckViolation,
    FeatureNotSupported,
    ForeignKeyViolation,
    InsufficientPrivilege,
    ProgramLimitExceeded,
    RaiseException,
    UniqueViolation,
)

from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.schema import migrate
from custom_memory_engine.security import privilege_violations, provision_runtime_role
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_lifecycle_transitions import insert_version, seed_source
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
    seed_turn,
)

pytestmark = pytest.mark.integration
M6 = 10
NEW_TABLES = ("association", "entity", "method", "abstraction_stats", "verification", "negative_constraint")
HASH = "a" * 64
WINDOW_A = "[2026-01-01,2026-02-01)"
WINDOW_B = "[2026-03-01,2026-04-01)"


async def add_method(conn: AsyncConnection, method_id: str = "m-test") -> None:
    await conn.execute(
        "INSERT INTO method (id, name, version, params_schema_hash) VALUES (%s, %s, '1', %s) ON CONFLICT DO NOTHING",
        (method_id, method_id, HASH),
    )


async def edge(conn: AsyncConnection, edge_id: str, a: str, b: str, kind: str = "related_to", **extra: str) -> None:
    await conn.execute(
        "INSERT INTO association (id, user_id, from_id, to_id, type, created_by, origin) "
        "VALUES (%s, 'u1', %s, %s, %s, 'test', %s)",
        (edge_id, a, b, kind, extra.get("origin", "tier_c")),
    )


async def abstraction(conn: AsyncConnection, version_id: str) -> None:
    """A believed abstraction with its ledger and a registered method (the explicit V2 way, not the v1 persona way)."""
    await add_method(conn)
    async with conn.transaction():
        await insert_version(conn, version_id, "abstraction", "contested")  # held; activating it derives `inferred`
        await conn.execute(
            "UPDATE memory_version SET status = 'active', recorded_range = tstzrange(clock_now(), NULL), "
            "method_id = 'm-test' WHERE id = %s",
            (version_id,),
        )
        await conn.execute(
            "INSERT INTO memory_evidence VALUES (%s, 't0', 'u1', 'I am vegetarian', 'support')", (version_id,)
        )
        await conn.execute("INSERT INTO memory_derivation VALUES (%s, 'src', 'u1', 'consolidation')", (version_id,))
        await conn.execute(
            "INSERT INTO abstraction_stats (version_id, user_id) VALUES (%s, 'u1') ON CONFLICT DO NOTHING",
            (version_id,),
        )


# ------------------------------ catalog


async def test_every_new_table_forces_row_level_security(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        found = await rows(
            conn,
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relnamespace = current_schema()::regnamespace AND relname = ANY(%s) ORDER BY 1",
            (list(NEW_TABLES),),
        )
        assert found == [(name, True, True) for name in sorted(NEW_TABLES)]
        policies = {
            row[0] for row in await rows(conn, "SELECT tablename FROM pg_policies WHERE schemaname = current_schema()")
        }
        assert set(NEW_TABLES) <= policies


async def test_stale_is_not_stored(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        columns = {
            row[0]
            for row in await rows(
                conn,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'abstraction_stats'",
            )
        }
        assert (
            "stale" not in columns and {"coverage", "evidence_generation", "closed_over_hash", "expires_at"} <= columns
        )


# ------------------------------ METHOD


async def test_method_rejects_update_delete_and_truncate_and_keeps_name_version_unique(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        assert [r[0] for r in await rows(conn, "SELECT id FROM method ORDER BY 1")] == [
            "m-v1-consolidation",
            "m-v1-corroboration",
            "m-v1-persona",
            "m-v1-re-derivation",
            "m-v1-summary",
        ]
        await add_method(conn)
        with pytest.raises(RaiseException, match="method_registry_is_append_only"):
            await conn.execute("UPDATE method SET description = 'x' WHERE id = 'm-test'")
        with pytest.raises(RaiseException, match="method_registry_is_append_only"):
            await conn.execute("DELETE FROM method WHERE id = 'm-test'")
        # PostgreSQL itself refuses to truncate a referenced table before the trigger runs; either way it is refused
        with pytest.raises((RaiseException, FeatureNotSupported)):
            await conn.execute("TRUNCATE method")
        with pytest.raises(
            UniqueViolation
        ):  # a version bump is a new row; the same name and version cannot be redefined
            await conn.execute(
                "INSERT INTO method (id, name, version, params_schema_hash) VALUES ('other', 'm-test', '1', %s)",
                ("b" * 64,),
            )
        await conn.execute(
            "INSERT INTO method (id, name, version, params_schema_hash) VALUES ('m-test-2', 'm-test', '2', %s)",
            (HASH,),
        )
        with pytest.raises(CheckViolation):
            await conn.execute(
                "INSERT INTO method (id, name, version, params_schema_hash) VALUES ('bad', 'n', '1', 'xyz')"
            )


# ------------------------------ ASSOCIATION


async def test_deleting_an_endpoint_deletes_the_association_and_never_touches_the_other_endpoint(database) -> None:  # noqa: F811
    _, schema = database
    async with connect(database) as conn:
        await seed_source(conn)
        await insert_version(conn, "x", "claim", "asserted")
        await insert_version(conn, "y", "claim", "asserted")
        await edge(conn, "e1", "x", "y", "contradicts")
        before = await rows(
            conn, "SELECT id, status, lifecycle, content, method_id FROM memory_version WHERE id = 'y'"
        )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", scope="memory", target_ref="x", schema_name=schema)
        assert receipt.deleted_versions == 1 and receipt.rederived_dependents == 0
        assert await rows(conn, "SELECT count(*) FROM association") == [(0,)]
        assert (
            await rows(conn, "SELECT id, status, lifecycle, content, method_id FROM memory_version WHERE id = 'y'")
            == before
        )


async def test_association_is_a_closed_typed_same_tenant_loop_free_edge_with_a_degree_cap(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await insert_version(conn, "a", "claim", "asserted")
        await insert_version(conn, "b", "claim", "asserted")
        with pytest.raises(CheckViolation):
            await edge(conn, "bad-type", "a", "b", "friend_of")
        with pytest.raises((CheckViolation, RaiseException)):  # the cap trigger names it first, the CHECK backs it up
            await edge(conn, "loop", "a", "a")
        with pytest.raises(CheckViolation):
            await conn.execute(
                "INSERT INTO association (id, user_id, from_id, to_id, type, weight, created_by) "
                "VALUES ('w', 'u1', 'a', 'b', 'related_to', 2, 't')"
            )
        with pytest.raises(ForeignKeyViolation):  # an endpoint must exist for the same tenant
            await edge(conn, "dangling", "a", "nobody")
        await edge(conn, "ok", "a", "b")
        with pytest.raises(UniqueViolation):
            await edge(conn, "dup", "a", "b")
        for number in range(31):  # the hub already has one edge; the cap (V2-D13) is 32 per artifact
            await insert_version(conn, f"s{number}", "claim", "asserted")
            await edge(conn, f"h{number}", "a", f"s{number}")
        await insert_version(conn, "overflow", "claim", "asserted")
        with pytest.raises(ProgramLimitExceeded):
            await edge(conn, "over", "a", "overflow")


# ------------------------------ ENTITY


async def test_an_entity_is_opaque_and_invisible_outside_its_owner_under_a_restricted_role(database) -> None:  # noqa: F811
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    try:
        async with connect(database) as admin:
            await admin.execute(
                "INSERT INTO app_user (id, external_ref, created_at) VALUES ('u4', 'x4', now()), ('u42', 'x42', now())"
            )
            await provision_runtime_role(admin, schema, role)
            assert await privilege_violations(admin, schema, role) == []  # the exact matrix, new tables included
        async with connect(database, user="u4") as app4:
            await app4.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            for name in ("Ada", "Ada"):  # same name twice: ids are random, never derived from content
                await app4.execute(
                    "INSERT INTO entity (user_id, scope_path, canonical_name, entity_type) "
                    "VALUES ('u4', '/u/u4/', %s, 'person')",
                    (name,),
                )
            ids = [str(r[0]) for r in await rows(app4, "SELECT id FROM entity ORDER BY id")]
            assert len(ids) == 2 and ids[0] != ids[1] and all(i.startswith("ent_") and len(i) == 36 for i in ids)
            with pytest.raises(CheckViolation):  # principal-scoped: the entity lives in the creator's own namespace
                await app4.execute(
                    "INSERT INTO entity (user_id, scope_path, canonical_name, entity_type) "
                    "VALUES ('u4', '/u/u42/', 'x', 't')"
                )
            with pytest.raises(Exception, match="row-level security"):  # and nobody can create one for someone else
                await app4.execute(
                    "INSERT INTO entity (user_id, scope_path, canonical_name, entity_type) "
                    "VALUES ('u42', '/u/u42/', 'x', 't')"
                )
        async with connect(
            database, user="u42"
        ) as app42:  # '/u/u4/' is a string prefix of nothing here, '/u/u42/' of '/u/u4'? no
            await app42.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            assert await rows(app42, "SELECT id FROM entity") == []
            real = await rows(app42, "SELECT count(*) FROM entity WHERE id = %s", (ids[0],))
            missing = await rows(app42, "SELECT count(*) FROM entity WHERE id = %s", ("ent_" + "0" * 32,))
            assert real == missing == [(0,)]  # indistinguishable from an id that does not exist
            with pytest.raises(InsufficientPrivilege):
                await app42.execute("DELETE FROM entity")
    finally:
        async with await AsyncConnection.connect(config.database_url.get_secret_value(), autocommit=True) as conn:
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


# ------------------------------ ledger, verification


async def test_the_ledger_must_balance_and_counts_cannot_be_negative(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await abstraction(conn, "abs1")
        for counts in ((1, 0, 0, 0), (0, 0, 0, 3), (2, 1, 1, 3)):
            with pytest.raises(CheckViolation, match="ledger_balances"):
                await conn.execute(
                    "UPDATE abstraction_stats SET support_count = %s, counter_count = %s, undetermined_count = %s, "
                    "opportunity_count = %s WHERE version_id = 'abs1'",
                    counts,
                )
        with pytest.raises(CheckViolation):
            await conn.execute(
                "UPDATE abstraction_stats SET support_count = -1, opportunity_count = -1 WHERE version_id = 'abs1'"
            )
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = 5, counter_count = 0, undetermined_count = 45, "
            "opportunity_count = 50 WHERE version_id = 'abs1'"
        )
        assert await rows(conn, "SELECT coverage FROM abstraction_stats WHERE version_id = 'abs1'") == [(0.1,)]
        with pytest.raises(CheckViolation, match="has_method"):
            await conn.execute("UPDATE abstraction_stats SET confidence = 0.5 WHERE version_id = 'abs1'")


async def test_stats_belong_to_an_abstraction_and_start_unverified(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        with pytest.raises(RaiseException, match="abstraction_stats_require_an_abstraction"):
            await conn.execute("INSERT INTO abstraction_stats (version_id, user_id) VALUES ('src', 'u1')")
        await abstraction(conn, "abs1")
        assert await rows(
            conn, "SELECT verification_status, support_count FROM abstraction_stats WHERE version_id = 'abs1'"
        ) == [("unverified", 0)]  # a new ledger starts unverified and empty


async def test_verification_is_a_record_and_the_status_cannot_claim_it_without_one(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await abstraction(conn, "abs1")
        with pytest.raises(RaiseException, match="requires_a_verification_record"):
            await conn.execute(
                "UPDATE abstraction_stats SET verification_status = 'verified' WHERE version_id = 'abs1'"
            )
        await conn.execute(
            "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
            "negative_control_slice_id, support_window, held_out_window, support_context_key, held_out_context_key, "
            "outcome) VALUES ('v-1', 'abs1', 'u1', 'split-1', now(), 'neg-1', %s::tstzrange, %s::tstzrange, "
            "'ctx-a', 'ctx-b', 'verified')",
            (WINDOW_A, WINDOW_B),
        )
        assert await rows(conn, "SELECT windows_disjoint FROM verification") == [(True,)]  # I11 data is checkable
        await conn.execute("UPDATE abstraction_stats SET verification_status = 'verified' WHERE version_id = 'abs1'")
        with pytest.raises(RaiseException, match="verification_cannot_be_withdrawn"):
            await conn.execute(
                "UPDATE abstraction_stats SET verification_status = 'unverified' WHERE version_id = 'abs1'"
            )
        with pytest.raises(RaiseException, match="verification_is_append_only"):
            await conn.execute("UPDATE verification SET outcome = 'refuted'")
        # append-only includes removal: a refuted record that could be deleted would leave the status claiming it
        with pytest.raises(RaiseException, match="verification_is_append_only"):
            await conn.execute("DELETE FROM verification WHERE id = 'v-1'")
        with pytest.raises(RaiseException, match="verification_is_append_only"):
            await conn.execute("TRUNCATE verification")
        with pytest.raises(
            RaiseException, match="requires_a_verification_record"
        ):  # a 'refuted' needs a refuting record
            await conn.execute(
                "UPDATE abstraction_stats SET verification_status = 'refuted' WHERE version_id = 'abs1'"
            )
        await conn.execute(
            "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
            "negative_control_slice_id, support_window, held_out_window, support_context_key, held_out_context_key, "
            "outcome) VALUES ('v-2', 'abs1', 'u1', 'split-2', now(), 'neg-2', %s::tstzrange, %s::tstzrange, "
            "'ctx-a', 'ctx-a', 'refuted')",
            (WINDOW_A, WINDOW_A),
        )
        assert await rows(conn, "SELECT windows_disjoint FROM verification WHERE id = 'v-2'") == [(False,)]


# ------------------------------ NEGATIVE_CONSTRAINT


async def test_a_negative_constraint_is_sticky_and_its_lift_is_a_complete_record(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute(
            "INSERT INTO negative_constraint "
            "(id, user_id, predicate_key, scope_path, method_family, dissented_at, reason) "
            "VALUES ('n1', 'u1', 'diet', '/u/u1/', 'cooccurrence', %s, 'user_dissent')",
            (START,),
        )
        with pytest.raises(UniqueViolation):  # one active constraint per (scope, predicate, method family)
            await conn.execute(
                "INSERT INTO negative_constraint "
                "(id, user_id, predicate_key, scope_path, method_family, dissented_at, "
                "reason) VALUES ('n2', 'u1', 'diet', '/u/u1/', 'cooccurrence', %s, 'again')",
                (START,),
            )
        with pytest.raises(CheckViolation, match="lift_is_complete"):  # who, when and which new evidence, together
            await conn.execute("UPDATE negative_constraint SET lifted_at = %s WHERE id = 'n1'", (START,))
        with pytest.raises(RaiseException, match="only_the_lift_may_change"):
            await conn.execute("UPDATE negative_constraint SET reason = 'edited' WHERE id = 'n1'")
        with pytest.raises(RaiseException, match="update_must_lift"):
            await conn.execute("UPDATE negative_constraint SET lifted_by = NULL WHERE id = 'n1'")
        await insert_version(conn, "fresh", "claim", "asserted")
        await conn.execute(
            "UPDATE negative_constraint SET lifted_at = %s, lifted_by = 'u1', lift_evidence_version_id = 'fresh' "
            "WHERE id = 'n1'",
            (START,),
        )
        with pytest.raises(RaiseException, match="already_lifted"):
            await conn.execute("UPDATE negative_constraint SET lifted_by = 'someone-else' WHERE id = 'n1'")


# ------------------------------ I1' and I15


async def test_an_inferred_row_needs_a_registered_method_and_it_is_fixed_once_set(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await add_method(conn)
        with pytest.raises(RaiseException, match="inferred_requires_method_record"):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, recorded_range, layer, kind, lifecycle, retention) VALUES "
                    "('h', 'h', 'u1', 'active', 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
                    "tstzrange(clock_now(), NULL), 'semantic', 'claim', 'inferred', 'durable')",
                    (START,),
                )
                await conn.execute("INSERT INTO memory_evidence VALUES ('h', 't0', 'u1', 'x', 'support')")
                await conn.execute("INSERT INTO memory_derivation VALUES ('h', 'src', 'u1', 'consolidation')")
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE id = 'h'") == [(0,)]
        async with conn.transaction():
            await insert_version(conn, "h", "claim", "inferred")  # the helper registers a method
            await conn.execute("UPDATE memory_version SET method_id = 'm-v1-consolidation' WHERE id = 'h' AND false")
        with pytest.raises(RaiseException, match="method_id_is_fixed_once_set"):
            await conn.execute("UPDATE memory_version SET method_id = 'm-test' WHERE id = 'h'")
        with pytest.raises(ForeignKeyViolation):  # only a registered method counts
            await conn.execute("UPDATE memory_version SET method_id = 'm-unregistered' WHERE id = 'src'")


async def test_v1_derived_claims_get_the_v1_method_of_their_derivation(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        async with conn.transaction():
            await insert_version(conn, "derived", "claim", "asserted")
            await conn.execute("INSERT INTO memory_derivation VALUES ('derived', 'src', 'u1', 'summary')")
        assert await rows(conn, "SELECT lifecycle, method_id FROM memory_version WHERE id = 'derived'") == [
            ("inferred", "m-v1-summary")
        ]


async def test_a_v1_persona_row_gets_the_persona_method_and_the_zero_ledger(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        async with conn.transaction():
            await insert_version(conn, "p", "abstraction", "inferred")
        assert await rows(conn, "SELECT method_id FROM memory_version WHERE id = 'p'") == [("m-v1-persona",)]
        assert await rows(
            conn,
            "SELECT support_count, counter_count, undetermined_count, opportunity_count, verification_status "
            "FROM abstraction_stats WHERE version_id = 'p'",
        ) == [(0, 0, 0, 0, "unverified")]  # o = 0 is "insufficient evidence" (V2-D49 D7)


async def test_an_abstraction_without_its_ledger_fails_at_commit(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await add_method(conn)
        with pytest.raises(RaiseException, match="abstraction_requires_a_ledger"):
            async with conn.transaction():
                await insert_version(conn, "abs", "abstraction", "contested")
                await conn.execute(
                    "UPDATE memory_version SET status = 'active', recorded_range = tstzrange(clock_now(), NULL), "
                    "method_id = 'm-test', layer = layer WHERE id = 'abs'"
                )
                await conn.execute("INSERT INTO memory_evidence VALUES ('abs', 't0', 'u1', 'x', 'support')")
                await conn.execute("INSERT INTO memory_derivation VALUES ('abs', 'src', 'u1', 'consolidation')")
                await conn.execute("DELETE FROM abstraction_stats WHERE version_id = 'abs'")


# ------------------------------ E-016 criterion 4


async def activate(conn: AsyncConnection, version_id: str, *, kind: str = "claim") -> None:
    """Move a held (contested) version to active, as v1 does on acceptance; the record is the caller's to add."""
    await conn.execute(
        "UPDATE memory_version SET status = 'active', recorded_range = tstzrange(clock_now(), NULL) WHERE id = %s",
        (version_id,),
    )
    await conn.execute(
        "INSERT INTO memory_evidence VALUES (%s, 't0', 'u1', 'I am vegetarian', 'support')", (version_id,)
    )


async def decision(conn: AsyncConnection, version_id: str, kind: str) -> None:
    await conn.execute(
        "INSERT INTO memory_conflict (id, candidate_version_id, user_id, relation, decision, reason_code, decided_by, "
        "decided_at) VALUES (%s, %s, 'u1', 'new_unrelated', %s, 'user_confirmed', 'memory-review', now())",
        (f"c-{version_id}-{kind}", version_id, kind),
    )


async def test_contested_to_asserted_needs_an_adjudication_record(database) -> None:  # noqa: F811
    """The measured defect of issue #67: this transition used to succeed with no record at all."""
    async with connect(database) as conn:
        await seed_source(conn)
        await insert_version(conn, "held", "claim", "contested")
        with pytest.raises(RaiseException, match="lifecycle_requires_adjudication_record"):
            async with conn.transaction():
                await activate(conn, "held")
        assert await rows(conn, "SELECT lifecycle FROM memory_version WHERE id = 'held'") == [("contested",)]
        async with conn.transaction():
            await activate(conn, "held")
            await decision(conn, "held", "accept")
        assert await rows(conn, "SELECT lifecycle FROM memory_version WHERE id = 'held'") == [("asserted",)]


async def test_contested_to_retired_needs_a_reason_code(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await insert_version(conn, "held", "claim", "contested")
        with pytest.raises(RaiseException, match="lifecycle_requires_reason_code"):
            async with conn.transaction():
                await conn.execute("UPDATE memory_version SET status = 'rejected' WHERE id = 'held'")
        async with conn.transaction():
            await conn.execute("UPDATE memory_version SET status = 'rejected' WHERE id = 'held'")
            await decision(conn, "held", "reject")
        assert await rows(conn, "SELECT lifecycle FROM memory_version WHERE id = 'held'") == [("retired",)]


async def test_asserted_to_retired_needs_the_recorded_time_close_or_an_archival_branch(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await insert_version(conn, "a", "claim", "asserted")
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        with pytest.raises(RaiseException, match="lifecycle_requires_recorded_time_close"):
            async with conn.transaction():
                await conn.execute("UPDATE memory_version SET lifecycle = 'retired' WHERE id = 'a'")
        await conn.execute("SELECT set_config('app.clock', '2026-09-01T00:00:00+00:00', false)")
        async with conn.transaction():
            await conn.execute(
                "UPDATE memory_version SET lifecycle = 'retired', recorded_range = tstzrange(lower(recorded_range), "
                "clock_now()) WHERE id = 'a'"
            )


async def test_inferred_to_retired_needs_one_of_the_reason_records(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        for version_id in ("bare", "refuted", "dissent"):
            await insert_version(conn, version_id, "claim", "inferred")
        with pytest.raises(RaiseException, match="lifecycle_requires_reason_code"):
            async with conn.transaction():
                await conn.execute("UPDATE memory_version SET lifecycle = 'retired' WHERE id = 'bare'")
        await seed_turn(conn, "t9", "x", START)
        async with conn.transaction():  # verification failed
            await conn.execute("UPDATE memory_version SET lifecycle = 'retired' WHERE id = 'refuted'")
            await conn.execute(
                "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
                "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                "held_out_context_key, outcome) VALUES ('vr', 'refuted', 'u1', 's', now(), 'n', "
                "%s::tstzrange, %s::tstzrange, 'a', 'b', 'refuted')",
                (WINDOW_A, WINDOW_B),
            )
        async with conn.transaction():  # the user's dissent (V2-D46)
            await conn.execute("UPDATE memory_version SET lifecycle = 'retired' WHERE id = 'dissent'")
            await conn.execute(
                "INSERT INTO negative_constraint (id, user_id, predicate_key, scope_path, method_family, "
                "dissented_at, reason, dissented_version_id) "
                "VALUES ('nc', 'u1', 'diet', '/u/u1/', 'f', now(), 'user_dissent', 'dissent')"
            )
        assert {r[0] for r in await rows(conn, "SELECT lifecycle FROM memory_version WHERE id <> 'src'")} == {
            "inferred",
            "retired",
        }


async def test_inferred_to_contested_needs_a_grounded_contradiction_and_a_tier_b_edge_does_not_count(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
        await insert_version(conn, "guess", "claim", "inferred")
        await insert_version(conn, "fact", "claim", "asserted")
        await edge(conn, "tier-b", "fact", "guess", "contradicts", origin="tier_b")  # V2-D48 C-P4: no channel effect
        with pytest.raises(RaiseException, match="lifecycle_requires_contradiction_record"):
            async with conn.transaction():
                await conn.execute("UPDATE memory_version SET lifecycle = 'contested' WHERE id = 'guess'")
        await edge(conn, "tier-c", "guess", "fact", "contradicts")
        async with conn.transaction():
            await conn.execute("UPDATE memory_version SET lifecycle = 'contested' WHERE id = 'guess'")


async def test_the_record_triggers_are_deferrable_and_listed_for_deletion(database) -> None:  # noqa: F811
    from custom_memory_engine.engine.deletion import ARCHIVAL_GUARD_TRIGGERS

    async with connect(database) as conn:
        assert (
            "trg_memory_version_transition_records" in ARCHIVAL_GUARD_TRIGGERS
        )  # r10: deletion judges the final state
        assert await rows(
            conn,
            "SELECT tgdeferrable, tginitdeferred FROM pg_trigger "
            "WHERE tgname = 'trg_memory_version_transition_records' AND NOT tgisinternal "
            "AND tgrelid = 'memory_version'::regclass",
        ) == [(True, True)]


# ------------------------------ deletion with the new references


async def test_deleting_a_version_a_dissent_names_keeps_the_dissent_and_succeeds(database) -> None:  # noqa: F811
    """A user must be able to delete a memory they dissented from.

    The dissented version's reference is cleared (ON DELETE SET NULL); the dissent stays sticky on the predicate.
    Before the guard allowed it this raised `negative_constraint_update_must_lift`, so the memory was undeletable.
    """
    config, schema = database
    async with connect(database) as conn:
        await seed_source(conn)
        await insert_version(conn, "dissented", "claim", "inferred")
        await conn.execute(
            "INSERT INTO negative_constraint (id, user_id, predicate_key, scope_path, method_family, dissented_at, "
            "reason, dissented_version_id) VALUES ('nc', 'u1', 'diet', '/u/u1/', 'f', %s, 'user_dissent', "
            "'dissented')",
            (START,),
        )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", scope="memory", target_ref="dissented", schema_name=schema)
        assert receipt.deleted_versions == 1
        assert await rows(conn, "SELECT id, dissented_version_id, lifted_at FROM negative_constraint") == [
            ("nc", None, None)
        ], "the dissent stays sticky; only the reference to the deleted version goes"


async def test_deleting_the_lift_evidence_withdraws_the_lift(database) -> None:  # noqa: F811
    """A lift is grounded in its evidence: deleting that evidence withdraws the lift, and the constraint is active."""
    config, schema = database
    async with connect(database) as conn:
        await seed_source(conn)
        await insert_version(conn, "evidence", "claim", "asserted")
        await conn.execute(
            "INSERT INTO negative_constraint (id, user_id, predicate_key, scope_path, method_family, dissented_at, "
            "reason) VALUES ('nc', 'u1', 'diet', '/u/u1/', 'f', %s, 'user_dissent')",
            (START,),
        )
        await conn.execute(
            "UPDATE negative_constraint SET lifted_at = %s, lifted_by = 'u1', lift_evidence_version_id = 'evidence' "
            "WHERE id = 'nc'",
            (START,),
        )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", scope="memory", target_ref="evidence", schema_name=schema)
        assert receipt.deleted_versions == 1
        assert await rows(conn, "SELECT lifted_at, lifted_by, lift_evidence_version_id FROM negative_constraint") == [
            (None, None, None)
        ], "a lift with no evidence behind it is not a lift"


# ------------------------------ the backfill under a non-bypass owner


async def test_the_backfill_reaches_rows_when_the_owner_cannot_bypass_rls(database) -> None:  # noqa: F811
    """The suite runs as a superuser, which bypasses RLS and would hide an empty backfill.

    A production upgrade is applied by an ordinary owner: FORCE ROW LEVEL SECURITY applies to it, so an unqualified
    ``UPDATE``/``INSERT ... SELECT`` matches nothing and every existing artifact keeps a NULL method with no ledger.
    This builds a version-9 schema owned by such a role, seeds it through the v1 write paths, and applies migration 10.
    """
    config, _ = database
    owner = "cme_owner_" + uuid4().hex[:12]
    password = uuid4().hex
    schema = "cme_m6_owner_" + uuid4().hex[:12]
    parameters = conninfo_to_dict(config.database_url.get_secret_value())
    database_name = str(parameters["dbname"])
    owner_dsn = make_conninfo(**{**parameters, "user": owner, "password": password})
    try:
        async with await AsyncConnection.connect(config.database_url.get_secret_value(), autocommit=True) as admin:
            await admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                    sql.Identifier(owner), sql.Literal(password)
                )
            )
            await admin.execute(
                sql.SQL("GRANT CREATE, CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name), sql.Identifier(owner)
                )
            )
            await admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(owner)))
        async with await AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            await conn.execute(
                sql.SQL("COMMENT ON SCHEMA {} IS {}").format(sql.Identifier(schema), sql.Literal(migrate.OWNER_MARKER))
            )
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            for _, source in migrate.migration_scripts(through=M6 - 1):
                cursor = await conn.execute(cast(LiteralString, source), prepare=False)
                while cursor.nextset():
                    pass
        async with await AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
                await conn.execute("SELECT set_config('app.clock', '2026-07-20T00:00:00+00:00', true)")
                await conn.execute(
                    "INSERT INTO app_user (id, external_ref, created_at) VALUES ('u1', 'ext-u1', clock_now())"
                )
                await conn.execute("INSERT INTO predicate_schema VALUES ('diet', 'single', 'semantic')")
                await conn.execute("INSERT INTO session VALUES ('s1', 'u1', clock_now())")
                await conn.execute(
                    "INSERT INTO turn VALUES ('t1', 's1', 'u1', 1, 'user', 'I am vegetarian', clock_now())"
                )
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, layer, recorded_range) VALUES ('source', 'mk-source', "
                    "'u1', 'active', 'u1', 'diet', false, 'vegetarian', tstzrange(clock_now(), NULL), 'semantic', "
                    "tstzrange(clock_now(), NULL))"
                )
                await conn.execute(
                    "INSERT INTO memory_evidence VALUES ('source', 't1', 'u1', 'vegetarian', 'support')"
                )
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, layer, recorded_range) VALUES ('derived', 'mk-derived', "
                    "'u1', 'active', 'u1', 'diet', false, 'vegan', tstzrange(clock_now(), NULL), 'semantic', "
                    "tstzrange(clock_now(), NULL))"
                )
                await conn.execute("INSERT INTO memory_evidence VALUES ('derived', 't1', 'u1', 'vegan', 'support')")
                await conn.execute("INSERT INTO memory_derivation VALUES ('derived', 'source', 'u1', 'consolidation')")
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, layer, recorded_range) VALUES ('persona', 'mk-persona', "
                    "'u1', 'active', 'u1', 'diet', false, 'vegan persona', tstzrange(clock_now(), NULL), 'persona', "
                    "tstzrange(clock_now(), NULL))"
                )
                await conn.execute("INSERT INTO memory_evidence VALUES ('persona', 't1', 'u1', 'vegan', 'support')")
        assert await rows_for(owner_dsn, schema, "SELECT count(*) FROM memory_version") == [(3,)]
        # the upgrade exactly as the runner applies it: search_path set, nothing else
        async with await AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            upgrade = migrate.migration_scripts(through=M6)[-1][1]
            cursor = await conn.execute(cast(LiteralString, upgrade), prepare=False)
            while cursor.nextset():
                pass
        assert await rows_for(owner_dsn, schema, "SELECT id, method_id FROM memory_version ORDER BY id") == [
            ("derived", "m-v1-consolidation"),
            ("persona", "m-v1-persona"),
            ("source", None),
        ], "the backfill did not reach the rows: it ran behind FORCE ROW LEVEL SECURITY"
        assert await rows_for(owner_dsn, schema, "SELECT version_id FROM abstraction_stats") == [("persona",)]
        async with await AsyncConnection.connect(owner_dsn, autocommit=True) as conn:  # and the row stays writable
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            await conn.execute("SELECT set_config('app.user_id', 'u1', false)")
            await conn.execute("SELECT set_config('app.clock', '2026-07-20T00:00:00+00:00', false)")
            async with conn.transaction():
                await conn.execute(
                    "UPDATE memory_version SET reinforcement_count = reinforcement_count + 1 WHERE id = 'derived'"
                )
    finally:
        async with await AsyncConnection.connect(config.database_url.get_secret_value(), autocommit=True) as admin:
            await admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            await admin.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(
                    sql.Identifier(database_name), sql.Identifier(owner)
                )
            )
            await admin.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(sql.Identifier(owner)))
            await admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner)))


async def rows_for(dsn: str, schema: str, statement: str, user: str = "u1") -> list[tuple[object, ...]]:
    async with await AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        # The owner is not exempt from FORCE ROW LEVEL SECURITY: without the identity it sees no rows at all.
        await conn.execute("SELECT set_config('app.user_id', %s, false)", (user,))
        return await rows(conn, statement)


# ------------------------------ rollback


async def test_rollback_restores_version_9_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await seed_source(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M6 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M6 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical to the pre-M6 schema
        assert await rows(conn, "SELECT to_regclass('association'), to_regclass('method')") == [(None, None)]
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(1,)]
    status = await runner.status()
    assert (status.state, status.version) == ("pending", M6 - 1)
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await rows(conn, "SELECT count(*) FROM method") == [(5,)]
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(1,)]
