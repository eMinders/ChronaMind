"""E-038 / V2-D24: ``stale`` is derived from the provenance graph, never stored.

The claim under test is *"``stale`` cannot disagree with the graph"*: a source is retired or deleted and the
dependent's ``stale`` flips **without anything writing a flag**, and there is no column a flag could be written to.
Each of the five
causes of ``CHANNELS.md`` 2.3 is shown to fire alone, the cache is shown to be invalidated inside the deletion
transaction (``DELETION_ORDER.md`` 5 row F6), and the hash is shown to be neither too stable nor too sensitive.
"""

import ast
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege

from custom_memory_engine.api.contract import OPERATIONS
from custom_memory_engine.api.entry import Call, CallerContext, Credential
from custom_memory_engine.api.read_handlers import ReadHandlers
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.engine import staleness as staleness_module
from custom_memory_engine.engine.channels import ArtifactView, StalenessInputs, assemble, stale_of
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.staleness import (
    ClosureCache,
    compute_closure,
    derive_staleness,
    digest_of,
    staleness_inputs,
    support_below_threshold,
)
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from tests.integration.test_lifecycle_transitions import insert_version, seed_source
from tests.integration.test_migration_m1 import (
    connect,
    database,  # noqa: F401  (fixture)
    rows,
)
from tests.integration.test_migration_m6 import WINDOW_A, WINDOW_B, abstraction
from tests.test_channels import KEY, OWN_SOURCE, READ_SET

pytestmark = pytest.mark.integration
NONE = StalenessInputs(False, False, False, False, False)
GOOD_LEDGER = (40, 0, 0)  # o = 40: confidence_lower 0.91 >= 0.90, coverage 1.0
SRC = Path(__file__).resolve().parents[2] / "src" / "custom_memory_engine"


async def seal(conn: AsyncConnection, cache: ClosureCache, schema: str, version_id: str = "abs1") -> str:
    """Verify ``version_id`` the way ``verify`` (C-021) will: a held-out record, the ledger, and the sealed closure."""
    closure = await cache.closure_of(conn, "u1", version_id, schema_name=schema)
    s, k, u = GOOD_LEDGER
    ver_id = "ver-" + version_id
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
            "negative_control_slice_id, support_window, held_out_window, support_context_key, "
            "held_out_context_key, outcome) "
            "VALUES (%s, %s, 'u1', 'split-1', now(), 'neg-1', %s::tstzrange, %s::tstzrange, 'ctx-a', 'ctx-b', "
            "'verified')",
            (ver_id, version_id, WINDOW_A, WINDOW_B),
        )
        await conn.execute(
            "INSERT INTO verification_artifact (verification_id, user_id, role, artifact_id) "
            "VALUES (%s, 'u1', 'support', %s), (%s, 'u1', 'held_out', %s)",
            (ver_id, f"sup-{version_id}", ver_id, f"held-{version_id}"),
        )
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = %s, counter_count = %s, undetermined_count = %s, "
            "opportunity_count = %s, refutation_attempts = 3, closed_over_hash = %s, "
            "verification_status = 'verified', confidence_method_id = 'm-test' WHERE version_id = %s",
            (s, k, u, s + k + u, closure.digest, version_id),
        )
    return closure.digest


async def verified_world(conn: AsyncConnection, schema: str) -> ClosureCache:
    """``src`` <- ``mid`` <- ``abs1``: a verified abstraction two levels above a grounded claim."""
    await seed_source(conn)
    await abstraction(conn, "abs1")  # derives from `src`
    await insert_version(conn, "mid", "claim", "inferred")  # derives from `src`
    await conn.execute("INSERT INTO memory_derivation VALUES ('abs1', 'mid', 'u1', 'consolidation')")
    cache = ClosureCache()
    await seal(conn, cache, schema)
    cache.clear()
    cache.computations = 0
    return cache


async def inputs(conn: AsyncConnection, schema: str, cache: ClosureCache, **kwargs: float) -> StalenessInputs:
    return await staleness_inputs(conn, "u1", "abs1", cache=cache, schema_name=schema, **kwargs)


async def retire(conn: AsyncConnection, version_id: str) -> None:
    await conn.execute("SELECT set_config('app.clock', '2026-09-01T00:00:00+00:00', false)")  # after it began
    await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_axes_dual_write")
    try:
        async with conn.transaction():
            await conn.execute(
                "UPDATE memory_version SET lifecycle = 'retired', "
                "recorded_range = tstzrange(lower(recorded_range), clock_now()) WHERE id = %s",
                (version_id,),
            )
    finally:
        await conn.execute("ALTER TABLE memory_version ENABLE TRIGGER trg_memory_version_axes_dual_write")


# ------------------------------------------------------------------------------------------------- criterion 1


async def test_stale_is_not_a_column_anywhere(database) -> None:  # noqa: F811
    """The negative test is the point: a contributor adding a ``stale`` column is the realistic regression."""
    async with connect(database) as conn:
        found = await rows(
            conn,
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND column_name ~* 'stale'",
        )
        assert found == []
        status_values = await rows(
            conn,
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'abstraction_stats'::regclass "
            "AND pg_get_constraintdef(oid) ~ 'verification_status'",
        )
        assert status_values and "stale" not in str(status_values).lower()


# ------------------------------------------------------------------------------------------------- criteria 2-5


async def test_a_fresh_verified_abstraction_is_not_stale(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        assert await inputs(conn, schema, cache) == NONE


async def test_closure_divergence_alone_flips_stale_without_writing_a_flag(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await conn.execute(
            "DELETE FROM memory_derivation WHERE derived_version_id = 'abs1' AND source_version_id = 'mid'"
        )
        before = await rows(conn, "SELECT * FROM abstraction_stats")
        got = await inputs(conn, schema, cache)
        assert got == StalenessInputs(True, False, False, False, False)  # the closure moved, nothing else did
        assert stale_of(got)
        assert await rows(conn, "SELECT * FROM abstraction_stats") == before  # derived: no row was written


async def test_a_verified_abstraction_with_no_sealed_hash_fails_closed(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await conn.execute("UPDATE abstraction_stats SET closed_over_hash = NULL WHERE version_id = 'abs1'")
        assert (await inputs(conn, schema, cache)).closure_changed


async def test_support_below_threshold_alone_flips_stale(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = 4, counter_count = 4, undetermined_count = 2, "
            "opportunity_count = 10 WHERE version_id = 'abs1'"
        )
        assert await inputs(conn, schema, cache) == StalenessInputs(False, True, False, False, False)


async def test_the_support_threshold_boundary_and_an_empty_ledger() -> None:
    assert not support_below_threshold(40, 0, 0)  # exactly the documented default passes
    assert support_below_threshold(35, 0, 0)  # o = 35: the zero-counter opportunity is too small (STATISTICS 4.2)
    assert support_below_threshold(0, 0, 0)  # an empty ledger supports nothing
    assert not support_below_threshold(40, 0, 0, tau_confidence=0.5, tau_coverage=0.5)  # the thresholds are parameters


async def test_a_retired_source_alone_flips_stale(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await retire(conn, "mid")  # a *transitive* source: abs1 cites it through no direct edge of its own row
        assert await inputs(conn, schema, cache) == StalenessInputs(False, False, True, False, False)


async def test_a_new_method_version_alone_flips_stale(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await conn.execute("SELECT set_config('app.clock', '2026-08-01T00:00:00+00:00', false)")
        await conn.execute(
            "INSERT INTO method (id, name, version, params_schema_hash) VALUES ('m-test-2', 'm-test', '2', %s)",
            ("b" * 64,),
        )
        assert await inputs(conn, schema, cache) == StalenessInputs(False, False, False, True, False)


async def test_expiry_alone_flips_stale_on_the_injected_clock(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await conn.execute(
            "UPDATE abstraction_stats SET expires_at = '2026-07-21T00:00:00+00:00' WHERE version_id = 'abs1'"
        )
        assert not (await inputs(conn, schema, cache)).expired  # the clock is 2026-07-20: not yet
        await conn.execute("SELECT set_config('app.clock', '2026-07-21T00:00:00+00:00', false)")
        assert await inputs(conn, schema, cache) == StalenessInputs(False, False, False, False, True)


async def test_an_unverified_abstraction_is_never_derived_stale(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        await seed_source(conn)
        await abstraction(conn, "abs1")  # unverified, no sealed hash
        assert await inputs(conn, schema, ClosureCache()) == NONE


# ------------------------------------------------------------------------------------------------- criterion 6 (F6)


async def test_closure_hash_cache_is_invalidated_inside_the_deletion_transaction(database) -> None:  # noqa: F811
    """F6: a deletion's effect is visible to the next read in the *same* transaction, with a warm cache."""
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        assert await inputs(conn, schema, cache) == NONE  # warms the cache under epoch 0
        assert cache.computations == 1
        async with conn.transaction():
            await delete_memory(conn, "u1", scope="memory", target_ref="mid", schema_name=schema)
            during = await inputs(conn, schema, cache)  # same transaction, before commit
        assert during.closure_changed and stale_of(during)
        assert cache.computations == 2  # the epoch moved, so it recomputed rather than serving the old hash
        assert (await inputs(conn, schema, cache)).closure_changed  # and it stays stale after commit
        assert cache.computations == 2  # the new epoch's entry is itself cached


async def test_a_rolled_back_deletion_leaves_the_cached_answer_valid(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await inputs(conn, schema, cache)
        async with conn.transaction(force_rollback=True):
            await delete_memory(conn, "u1", scope="memory", target_ref="mid", schema_name=schema)
        assert await inputs(conn, schema, cache) == NONE
        assert (
            cache.computations == 1
        )  # the epoch rolled back with the transaction, so the entry is still the right one


# ------------------------------------------------------------------------------------------------- criterion 7


async def test_the_hash_changes_when_a_deep_ancestor_changes(database) -> None:  # noqa: F811
    """Too stable would miss this: a change five levels down must move the digest."""
    async with connect(database) as conn:
        _, schema = database
        await seed_source(conn)
        parent = "src"
        for depth in range(1, 6):
            await insert_version(conn, f"d{depth}", "claim", "inferred")
            await conn.execute(
                "INSERT INTO memory_derivation VALUES (%s, %s, 'u1', 'consolidation') ON CONFLICT DO NOTHING",
                (f"d{depth}", parent),
            )
            parent = f"d{depth}"
        before = await compute_closure(conn, "u1", "d5", schema_name=schema)
        assert set(before.sources) == {"src", "d1", "d2", "d3", "d4"}  # unbounded, not MAX_LINEAGE_DEPTH = 3
        await conn.execute("ALTER TABLE memory_version DISABLE TRIGGER USER")
        await conn.execute("UPDATE memory_version SET content = 'changed' WHERE id = 'd1'")  # simulates a replaced row
        await conn.execute("ALTER TABLE memory_version ENABLE TRIGGER USER")
        assert (await compute_closure(conn, "u1", "d5", schema_name=schema)).digest != before.digest


async def test_the_hash_ignores_unrelated_changes_and_insertion_order(database) -> None:  # noqa: F811
    """Too sensitive would flag these: nothing the artifact was derived from has changed."""
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        sealed = (await cache.closure_of(conn, "u1", "abs1", schema_name=schema)).digest
        await conn.execute("UPDATE memory_version SET reinforcement_count = reinforcement_count + 1 WHERE id = 'src'")
        await insert_version(conn, "unrelated", "claim", "asserted")  # an artifact outside the closure
        await conn.execute("SELECT set_config('app.clock', '2026-09-01T00:00:00+00:00', false)")
        assert (await compute_closure(conn, "u1", "abs1", schema_name=schema)).digest == sealed
        assert await inputs(conn, schema, cache) == NONE


def test_the_digest_is_order_independent_total_and_versioned() -> None:
    pairs = [("b", "two"), ("a", "one"), ("c", "three")]
    assert digest_of(pairs) == digest_of(reversed(pairs))
    assert digest_of([]) == digest_of(())  # total: no sources is a value, not an error
    assert digest_of(pairs) != digest_of(pairs[:2])
    assert digest_of([("a", "one")]) != digest_of([("a", "ONE")])
    assert digest_of([("a", "café")]) == digest_of([("a", "café")])  # NFC, the V2-D41 convention


# ------------------------------------------------------------------------------------------------- criterion 8


def test_only_the_derivation_module_builds_staleness_inputs() -> None:
    """No caller can substitute the inputs: ``staleness.py`` is the single construction site in ``src/``."""
    builders: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "StalenessInputs"
            ):
                builders.add(path.relative_to(SRC).as_posix())
    assert builders == {"engine/staleness.py"}
    assert staleness_module.__doc__ and "never stored" in staleness_module.__doc__


# ------------------------------------------------------------------------------------------------- criterion 9


async def test_a_stale_verified_abstraction_renders_in_hypotheses_through_assemble(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        view = ArtifactView("abs1", "abstraction", "inferred", "verified", "the user is vegetarian", (OWN_SOURCE,))

        async def render() -> tuple[int, int]:
            fn = await derive_staleness(conn, "u1", [view], cache=cache, schema_name=schema)
            result = assemble([view], caller_user_id="u1", read_set=READ_SET, key=KEY, staleness=fn)
            return len(result.facts), len(result.hypotheses)

        # a retired transitive source makes the verified abstraction stale
        await retire(conn, "mid")
        facts, hypotheses = await render()
        assert (facts, hypotheses) == (
            0,
            1,
        )  # C-P2: verified and stale is a hypothesis, routed by the existing channel code


async def test_an_unknown_verified_view_defaults_to_stale_not_fresh(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        known = ArtifactView("abs1", "abstraction", "inferred", "verified", "x", (OWN_SOURCE,))
        stranger = ArtifactView("never-asked", "abstraction", "inferred", "verified", "x", (OWN_SOURCE,))
        fn = await derive_staleness(conn, "u1", [known], cache=cache, schema_name=schema)
        assert fn(known) is False and fn(stranger) is True


# ------------------------------------------------------------------------------------------------- criterion 10


async def test_a_mismatched_tenant_context_is_refused(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        with pytest.raises(InsufficientPrivilege, match="staleness_user_context_mismatch"):
            await staleness_inputs(conn, "u2", "abs1", cache=cache, schema_name=schema)
        with pytest.raises(InsufficientPrivilege):
            await cache.closure_of(conn, "", "abs1", schema_name=schema)


async def test_the_hash_does_not_cross_tenants(database) -> None:  # noqa: F811
    """As another tenant's restricted role, u1's artifact has an empty closure: its ancestors are not visible (I7)."""
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        role = "cme_rt_" + uuid4().hex[:12]
        await conn.execute("INSERT INTO app_user VALUES ('u2', 'ext-u2', now()) ON CONFLICT DO NOTHING")
        await provision_runtime_role(conn, schema, role)
        mine = await compute_closure(conn, "u1", "abs1", schema_name=schema)
        assert set(mine.sources) == {"src", "mid"}
        async with tenant_scope(conn, "u2", role=role):
            theirs = await compute_closure(conn, "u2", "abs1", schema_name=schema)
            assert (await staleness_inputs(conn, "u2", "abs1", cache=cache, schema_name=schema)) == NONE
        assert theirs.sources == () and theirs.digest == digest_of([]) and theirs.digest != mine.digest


# ------------------------------------------------------------------------------------------------- criteria 12-13


async def test_repeated_reads_compute_the_hash_once(database) -> None:  # noqa: F811
    """W9: N reads of an unchanged artifact walk the graph once; with the cache disabled they walk N times."""
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        for _ in range(25):
            assert await inputs(conn, schema, cache) == NONE
        assert cache.computations == 1
        uncached = ClosureCache(max_entries=0)
        for _ in range(25):
            await inputs(conn, schema, uncached)
        assert uncached.computations == 25  # the control: removing the cache makes the probe fail


async def test_eviction_does_not_change_the_answer_and_the_cache_is_bounded(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        small = ClosureCache(max_entries=1)
        first = await inputs(conn, schema, small)
        await small.closure_of(conn, "u1", "mid", schema_name=schema)  # evicts abs1
        assert len(small) == 1
        assert await inputs(conn, schema, small) == first == await inputs(conn, schema, cache)
        assert small.computations == 3  # the eviction cost a recomputation, not a different value


async def test_the_cache_is_keyed_by_schema_user_artifact_and_deletion_token(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await inputs(conn, schema, cache)
        assert cache.keys() == [(schema, "u1", "abs1", "0:")]  # no deletion yet: epoch 0, no request id


async def test_a_reused_deletion_epoch_does_not_serve_a_stale_cache_entry(database) -> None:  # noqa: F811
    """The epoch is ``MAX + 1``, so a rollback returns it to the pool and a later deletion reuses it.

    Keying on the epoch alone then serves the entry written during the rolled-back request to a real, unrelated
    deletion: the source is gone but ``closure_changed`` reads ``False``. The ``uuid4`` request id in the token is
    what makes a recycled epoch harmless (``E-038`` review finding).
    """
    async with connect(database) as conn:
        _, schema = database
        cache = await verified_world(conn, schema)
        await insert_version(conn, "other", "claim", "asserted")  # NOT an ancestor of abs1
        assert not (await inputs(conn, schema, cache)).closure_changed

        # T1: a deletion of an unrelated artifact that ROLLS BACK. It allocates epoch 1 and warms the cache at it.
        async with conn.transaction(force_rollback=True):
            await delete_memory(conn, "u1", scope="memory", target_ref="other", schema_name=schema)
            assert not (await inputs(conn, schema, cache)).closure_changed  # abs1 is untouched by this deletion
            rolled_back_keys = [key for key in cache.keys() if key[3].startswith("1:")]
            assert rolled_back_keys, "the rolled-back request should have warmed an epoch-1 entry"

        # T2: the real deletion of abs1's ancestor. MAX(epoch) is back to 0, so this is epoch 1 *again*.
        async with conn.transaction():
            await delete_memory(conn, "u1", scope="memory", target_ref="mid", schema_name=schema)
            during = await inputs(conn, schema, cache)
        assert during.closure_changed, "a real deletion must never read as fresh"
        assert stale_of(during)
        # The rolled-back entry is still in the cache and simply never matches again: its request id differs.
        assert [key for key in cache.keys() if key[3].startswith("1:")] != rolled_back_keys
        live = await compute_closure(conn, "u1", "abs1", schema_name=schema)
        assert live.sources == ("src",), "mid is gone, so the live closure is narrower than the sealed one"


# ------------------------------------------------------------------------------ E-038-F5: consumed by the read path

OP = {operation.name: operation for operation in OPERATIONS}


async def read_handlers_for(database, conn: AsyncConnection) -> tuple[ReadHandlers, CallerContext, str]:  # noqa: F811
    """``ReadHandlers`` bound to the synthetic tenant ``u1`` under a restricted runtime role, as production does."""
    _, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    await provision_runtime_role(conn, schema, role)
    read_set = resolve_read_set("u1", [Selector.of("/u/u1/")], [])
    ctx = CallerContext.from_credential(Credential("u1", "C", False), write_scope="/u/u1/", read_set=read_set)
    return ReadHandlers(Runtime(conn, role, schema)), ctx, role


async def drop_role(conn: AsyncConnection, role: str) -> None:
    await conn.execute("RESET ROLE")
    await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))


async def test_the_read_path_renders_a_stale_verified_abstraction_in_hypotheses(database) -> None:  # noqa: F811
    """``E-038-F5``/``C-P3``: ``get`` and ``query`` consume the derivation, so a stale verified abstraction is a
    hypothesis on the public read path and not only through ``assemble``.

    Before this test the read path passed ``stale=False`` unconditionally: a withdrawn source left the abstraction
    rendering as a **fact** through both ``get`` and ``query``, which is exactly the ``V2-D24`` hole staleness exists
    to close. The fresh case is asserted first, so the stale case cannot pass by the path refusing everything.
    """
    async with connect(database) as conn:
        _, schema = database
        await verified_world(conn, schema)
        # Complete the bound (R3/I14): a fresh verified abstraction cannot render in ``facts`` without its bound.
        await conn.execute(
            "UPDATE abstraction_stats SET confidence = 0.91, expires_at = clock_now() + interval '30 days' "
            "WHERE version_id = 'abs1'"
        )
        handlers, ctx, role = await read_handlers_for(database, conn)
        try:
            fresh = await handlers.get(Call(ctx=ctx, operation=OP["get"], arguments={"artifact_id": "abs1"}))
            assert fresh.channel == "facts", "a fresh verified abstraction must still render as a fact"
            assert fresh.bound is not None, "a fresh verified abstraction in facts must carry its bound (I14)"
            await retire(conn, "mid")  # a transitive source is withdrawn: abs1 is now stale (C-P3)
            stale = await handlers.get(Call(ctx=ctx, operation=OP["get"], arguments={"artifact_id": "abs1"}))
            assert stale.channel == "hypotheses", "a stale verified abstraction rendered as a fact through get"
            assert stale.verification == "verified", "stale and verified is a legal pair, not a downgrade"
            assert stale.label == "verified, needs re-verification"
            result = await handlers.query(Call(ctx=ctx, operation=OP["query"], arguments={"request": {}}))
            assert [item.artifact_id for item in result.hypotheses] == ["abs1"]
            assert "abs1" not in {item.artifact_id for item in result.facts}
        finally:
            await drop_role(conn, role)


async def test_the_read_path_does_not_consult_staleness_for_an_audit(database, monkeypatch) -> None:  # noqa: F811
    """``E-038-F3``: ``audit_as_of`` never derives present staleness — asserted by making the derivation explode.

    Derived staleness decides a *channel*, and an audit row reaches ``audit[]`` whatever its channel is, so asserting
    the audit's contents cannot distinguish "did not ask" from "asked and it did not matter". The claim that matters is
    that the derivation is **not consulted at all**, so this replaces it with a function that raises: an audit that
    completes proves the path never reached it. A ``current_state`` query in the same world proves the replacement was
    armed, so the audit's success is not vacuous.
    """

    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("staleness was derived for an audit query (E-038-F3)")

    async with connect(database) as conn:
        _, schema = database
        await verified_world(conn, schema)
        handlers, ctx, role = await read_handlers_for(database, conn)
        try:
            ((belief,),) = await rows(conn, "SELECT pg_catalog.transaction_timestamp()")
            assert isinstance(belief, datetime)
            await retire(conn, "mid")
            request = {"mode": "audit_as_of", "target_time": belief.isoformat(), "belief_time": belief.isoformat()}
            monkeypatch.setattr(type(handlers), "_stale_ids", refuse)
            result = await handlers.query(Call(ctx=ctx, operation=OP["query"], arguments={"request": request}))
            assert result.is_audit and "abs1" in {item.artifact_id for item in result.audit}
            # the same world, an answer route: this one must reach the (armed) derivation
            with pytest.raises(AssertionError, match="derived for an audit"):
                await handlers.query(Call(ctx=ctx, operation=OP["query"], arguments={"request": {}}))
            monkeypatch.undo()
        finally:
            await drop_role(conn, role)
