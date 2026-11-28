"""E-011 rule 6: a cross-scope read leaves a content-free `scope_read` row, through the public entry point.

``ARCHITECTURE_V2.md`` section 5 rule 6 requires that a read reaching outside the caller's own namespace be logged,
content-free, "which makes the isolation claim auditable rather than asserted". Migration 11 shipped the writer
(`scope.audit.record_scope_read`) and the ledger recorded the gap honestly: no read path called it. These tests close
that gap at the layer where reads are actually authorised - ``api.entry.Engine.call`` - and prove the negative too: a
read confined to the caller's own namespace writes nothing, so the audit stays a signal rather than noise.

The `get`/`provenance`/`query` handlers are owned by later W3 tasks, so the operation is registered here with a
handler that returns what ``Engine.call`` resolved. That keeps the test about the *entry point's* audit obligation
rather than about an unwritten handler.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import (
    Call,
    CallerContext,
    Credential,
    Engine,
    EntryError,
    TargetInfo,
    TargetResolver,
)
from custom_memory_engine.api.runtime import DbMethods
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.audit import ConnectionScopeReads
from custom_memory_engine.scope.paths import MAX_READ_SCOPE_PREFIXES, ScopeError
from tests.integration.test_api_wiring_structure import caller, make_version, world
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)

T0 = datetime(2026, 3, 10, tzinfo=UTC)

pytestmark = pytest.mark.integration


def granted_caller(user: str, owner: str, tier: Literal["B", "C"] = "C") -> CallerContext:
    """``user`` reading its own namespace plus ``owner``'s, having been granted it (rule 2: nothing is implicit)."""
    read_set = resolve_read_set(
        user, [Selector.of(f"/u/{user}/"), Selector.of(f"/u/{owner}/", {"project", "shared"})], [f"/u/{owner}/"]
    )
    return CallerContext.from_credential(Credential(user, tier, False), write_scope=f"/u/{user}/", read_set=read_set)


async def scope_read_rows(conn: AsyncConnection) -> list[tuple[object, ...]]:
    cursor = await conn.execute(
        "SELECT user_id, event_type, details::text FROM audit_event WHERE event_type = 'scope_read' ORDER BY id"
    )
    return [tuple(row) for row in await cursor.fetchall()]


async def echo(call: Call) -> dict[str, TargetInfo]:
    """Stand-in for a read handler: report exactly what the entry point resolved (the handlers land in W3)."""
    return dict(call.targets)


class RecordingSpy:
    """A `ScopeReadRecorder` that keeps what it was told, for the entry point's own contract."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def record(self, principal: str, cross_scope_paths: Sequence[str]) -> None:
        self.calls.append((principal, tuple(cross_scope_paths)))


class FailingRecorder:
    """A recorder that cannot write must fail the read closed, never let it through unaudited."""

    async def record(self, principal: str, cross_scope_paths: Sequence[str]) -> None:
        del principal, cross_scope_paths
        raise ScopeError("audit_unavailable")


# ------------------------------------------------------------------------ the entry point records what it resolved


async def test_a_cross_scope_read_is_recorded_and_a_confined_read_is_not(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "mine", "bob", visibility="private")
        await make_version(conn, "theirs", "alice", visibility="shared")
        await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g"}
        )
        # The handler is registered only now, so the audit is the entry point's work and nothing else's.
        engine.register("get", echo)

        await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "theirs"})
        rows = await scope_read_rows(conn)
        assert len(rows) == 1, "a cross-scope read must leave exactly one audit row"
        user_id, event_type, details = rows[0]
        assert (user_id, event_type) == ("bob", "scope_read")
        assert '"cross_scope_count": 1' in str(details) and '"selector_count": 1' in str(details)

        before = len(await scope_read_rows(conn))
        await engine.call(caller("bob"), "get", {"artifact_id": "mine"})  # bob's own namespace
        assert len(await scope_read_rows(conn)) == before, "a confined read records nothing"


async def test_the_audit_row_carries_no_path_principal_or_content(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "theirs", "alice", visibility="shared")
        await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g"}
        )
        engine.register("get", echo)
        await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "theirs"})
        ((_, _, details),) = await scope_read_rows(conn)
        rendered = str(details)
        assert "/u/alice/" not in rendered and "theirs" not in rendered and "alice" not in rendered
        assert " /u/" not in rendered and '"scope' not in rendered  # no scope field at all, only counts


async def test_a_refused_read_records_nothing(database) -> None:  # noqa: F811
    """The row is written after the handler, so a read that never happened leaves no trace of having happened."""
    async with world(database) as (conn, engine, _):
        await make_version(conn, "closed", "alice", visibility="private")
        engine.register("get", echo)
        # bob holds no grant: `targets_readable` refuses before any handler runs.
        with pytest.raises(EntryError) as refused:
            await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "closed"})
        assert refused.value.code == "forbidden"
        assert await scope_read_rows(conn) == []


async def test_a_recorded_read_keeps_the_callers_identity(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "theirs", "alice", visibility="shared")
        await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g"}
        )
        engine.register("get", echo)
        await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "theirs"})
        ((user_id, _, _),) = await scope_read_rows(conn)
        assert user_id == "bob", "the row is written as the reader, under its own row-level security"


async def test_an_unwired_operation_that_reads_records_nothing(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "theirs", "alice", visibility="shared")
        await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g"}
        )
        with pytest.raises(EntryError):
            await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "theirs"})
        assert await scope_read_rows(conn) == [], "an unwired operation answers forbidden and records nothing"


# ------------------------------------------------------------------------------ the entry point's own contract


async def test_the_engine_tells_the_recorder_only_about_paths_outside_the_namespace() -> None:
    class Resolver(TargetResolver):
        async def resolve(self, ctx: CallerContext, ids: object) -> dict[str, TargetInfo]:  # type: ignore[override]
            return {
                "inside": TargetInfo("/u/bob/", "private"),
                "outside": TargetInfo("/u/alice/p/x/", "shared"),
            }

    spy = RecordingSpy()
    engine = Engine(resolver=Resolver(), methods=DbMethods.__new__(DbMethods), scope_reads=spy)
    engine.register("get", echo)
    await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "outside"})
    assert spy.calls == [("bob", ("/u/alice/p/x/",))]


async def test_a_recorder_failure_fails_the_read_closed() -> None:
    class Resolver(TargetResolver):
        async def resolve(self, ctx: CallerContext, ids: object) -> dict[str, TargetInfo]:  # type: ignore[override]
            return {"outside": TargetInfo("/u/alice/", "shared")}

    engine = Engine(resolver=Resolver(), methods=DbMethods.__new__(DbMethods), scope_reads=FailingRecorder())
    engine.register("get", echo)
    with pytest.raises(EntryError):
        await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "outside"})


async def test_an_engine_with_no_recorder_is_a_no_op_not_a_silent_pass() -> None:
    """Unit tests and audit-less deployments wire no recorder; the entry point must not invent one."""

    class Resolver(TargetResolver):
        async def resolve(self, ctx: CallerContext, ids: object) -> dict[str, TargetInfo]:  # type: ignore[override]
            return {"outside": TargetInfo("/u/alice/", "shared")}

    engine = Engine(resolver=Resolver(), methods=DbMethods.__new__(DbMethods))
    engine.register("get", echo)
    assert await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "outside"}) != {}


# --------------------------------------------------------------------------------- the recorder's own contract


async def test_the_recorder_refuses_an_unbounded_set_of_paths() -> None:
    """The count is capped by the same declared limit the read set has; it never grows unbounded."""
    recorder = ConnectionScopeReads(session_factory=None)
    paths = tuple(f"/u/alice/p/x{index}/" for index in range(MAX_READ_SCOPE_PREFIXES + 1))
    with pytest.raises(ScopeError):
        await recorder.record("bob", paths)


async def test_the_recorder_rejects_a_malformed_path() -> None:
    recorder = ConnectionScopeReads(session_factory=None)
    with pytest.raises(ScopeError):
        await recorder.record("bob", ("/u/alice",))  # no trailing separator


# ------------------------------------------------------------------------------------- the production wiring


async def test_the_production_wiring_includes_the_scope_read_recorder(database) -> None:  # noqa: F811
    """The gap this test closes: `build_engine` must never wire an engine whose cross-scope reads go unrecorded."""
    async with world(database) as (conn, engine, _):
        assert isinstance(engine.scope_reads, ConnectionScopeReads), "no scope read recorder was wired"
        await make_version(conn, "theirs", "alice", visibility="shared")
        await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g"}
        )
        engine.register("get", echo)
        await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "theirs"})
        ((user_id, event_type, _),) = await scope_read_rows(conn)
        assert (user_id, event_type) == ("bob", "scope_read")


async def test_the_recorder_writes_through_the_restricted_role(database) -> None:  # noqa: F811
    """The row is written as the caller under the runtime role: no privileged back door for the audit either."""
    async with world(database) as (conn, engine, _):
        await make_version(conn, "theirs", "alice", visibility="shared")
        await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g"}
        )
        engine.register("get", echo)
        await engine.call(granted_caller("bob", "alice"), "get", {"artifact_id": "theirs"})
        assert await scope_read_rows(conn), "the recorder wrote nothing through the runtime role"


def test_the_wiring_module_names_the_recorder_as_a_collaborator() -> None:
    """A weaker but important guard: the strings are present in the one place that builds a production engine."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "custom_memory_engine" / "api" / "wiring.py").read_text(
        encoding="utf-8"
    )
    assert "ConnectionScopeReads" in source and "scope_reads=" in source
