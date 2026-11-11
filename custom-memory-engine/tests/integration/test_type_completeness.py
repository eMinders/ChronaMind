# ruff: noqa: E501, F811  (long SQL/prose lines; `database` is a re-imported pytest fixture)
"""E-018 / gate G1: type-completeness - every requested memory type is a configuration of the V2 axes.

The claim is *tested*, not argued. The legal ``(kind x retention x lifecycle x facet)`` tuples are **derived** from the
authoritative rules (``model.axes``), never hand-listed, and each one is exercised end to end against the real migrated schema
as a ``NOSUPERUSER NOBYPASSRLS`` role. Each property is its own assertion so a failure names it:

* **storable**   the database accepts the tuple, with every record its lifecycle requires;
* **queryable**  the owner reads it back through row-level security, and a believed tuple appears in the temporal ``current_state``;
* **deletable**  ``delete_memory`` removes it and its derived dependent (the closure);
* **routed**     the channel of ``CHANNELS.md`` is the one the independent reference model gives.

Illegal tuples (every retention-floor violation included, and the facet ``inferred_preference``) are refused by the database.

Honest scope: ``Engine.call`` drives what is wired today (``ingest_episode``, ``assert``); the other properties are measured
below the entry point. ``delete``, ``query`` and procedure creation are wired by W3 (``E-037``, ``E-033``, ``E-030``), and an
unwired operation is *not yet testable*, never a pass. ``tests/test_channel_membership_model.py`` (channels), ``model.axes`` (rules)
and ``tests/profile_branching.py`` (no branching on a profile) are the independent oracles.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, RaiseException

from custom_memory_engine.api.contract import OPERATIONS
from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.infer_handlers import register_infer_handlers
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import Receipt
from custom_memory_engine.engine.channels import Verification, channel_of
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.predicates import current_state, fetch_versions
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.model.axes import (
    FACETS,
    KINDS,
    LEGAL_LIFECYCLE,
    LIFECYCLES,
    RETENTIONS,
    SHORT_RETENTION_KINDS,
    AxisError,
    Kind,
    Lifecycle,
    Retention,
    validate_axes,
)
from custom_memory_engine.profile import Profile, conversational, parse_profile
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from tests.integration.test_api_wiring_structure import (
    caller,
    database,  # noqa: F401  (fixture)
    world,
)
from tests.integration.test_migration_m1 import START, rows
from tests.profile_branching import find_profile_branching
from tests.test_channel_membership_model import Item
from tests.test_channel_membership_model import channel_of as reference_channel

pytestmark = pytest.mark.integration

type Facet = str | None
FACET_VALUES: tuple[Facet, ...] = (None, *FACETS)
LAYER = {"episode": "episodic", "claim": "semantic", "procedure": "semantic", "abstraction": "persona"}
STATUS = {
    "observed": "active",
    "asserted": "active",
    "inferred": "active",
    "contested": "conflict_pending",
    "retired": "rejected",
}
GROUNDED = ("observed", "asserted")
NOW = datetime(2030, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Tuple4:
    kind: Kind
    retention: Retention
    lifecycle: Lifecycle
    facet: Facet

    def __str__(self) -> str:
        return f"{self.kind}/{self.retention}/{self.lifecycle}/{self.facet}"


def is_legal(kind: Kind, retention: Retention, lifecycle: Lifecycle, facet: Facet) -> bool:
    """The legality rules, stated once, from the model: lifecycle by kind (V2-D48), the floor (V2-D29), the facet set."""
    return (
        lifecycle in LEGAL_LIFECYCLE[kind]
        and (retention == "durable" or kind in SHORT_RETENTION_KINDS)
        and (facet is None or facet in FACETS)
    )


LEGAL = [
    Tuple4(k, r, lc, f)
    for k, r, lc, f in product(KINDS, RETENTIONS, LIFECYCLES, FACET_VALUES)
    if is_legal(k, r, lc, f)
]
ILLEGAL = [
    Tuple4(k, r, lc, f)
    for k, r, lc, f in product(KINDS, RETENTIONS, LIFECYCLES, FACET_VALUES)
    if not is_legal(k, r, lc, f)
]


def verification_of(lifecycle: Lifecycle) -> Verification:
    return "unverified" if lifecycle == "inferred" else "none"


# ------------------------------------------------------------------------------------------------- the derivation


def test_the_legal_tuples_are_derived_from_the_model_and_agree_with_validate_axes() -> None:
    """No hand-written list: the set is the rules' output, and ``validate_axes`` accepts exactly it."""
    accepted: set[tuple[str, str, str]] = set()
    for kind, retention, lifecycle in product(KINDS, RETENTIONS, LIFECYCLES):
        try:
            validate_axes(kind, lifecycle, retention)
        except AxisError:
            continue
        accepted.add((kind, retention, lifecycle))
    assert {(t.kind, t.retention, t.lifecycle) for t in LEGAL} == accepted
    # the closed-form count, so a change to a rule changes this number visibly: (lifecycles x retentions) per kind, x facets
    per_kind = {kind: len(LEGAL_LIFECYCLE[kind]) * (3 if kind in SHORT_RETENTION_KINDS else 1) for kind in KINDS}
    assert len(LEGAL) == sum(per_kind.values()) * len(FACET_VALUES)
    print(
        f"\nG1: {len(LEGAL)} legal (kind x retention x lifecycle x facet) tuples, {len(ILLEGAL)} illegal of {len(LEGAL) + len(ILLEGAL)}"
    )
    assert len(LEGAL) == 224 and len(ILLEGAL) == 196


def test_the_facet_enum_is_exactly_the_six_canonical_values() -> None:
    assert FACETS == ("preference", "goal", "constraint", "context", "decision_pattern", "meta_preference")
    assert "inferred_preference" not in FACETS and len(set(FACETS)) == 6


# ------------------------------------------------------------------------------------------------- the real schema


@asynccontextmanager
async def env(database) -> AsyncIterator[tuple[AsyncConnection, str, str, str]]:  # noqa: F811
    """A migrated schema with one user, the restricted role, and a base claim the tuples can derive from."""
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with world(database) as (conn, engine, _schema):
        del engine
        await conn.execute("SELECT set_config('app.clock', %s, false)", (START.isoformat(),))
        try:
            yield conn, schema, role, "alice"
        finally:  # tests provision this second role themselves; roles are cluster-wide, so it must not outlive them
            await conn.execute("RESET ROLE")
            if await rows(conn, "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)):
                await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def insert_tuple(conn: AsyncConnection, tid: str, t: Tuple4, *, user: str = "alice") -> None:
    """One row of tuple ``t`` with every record its lifecycle needs, inside the caller's transaction."""
    believed = STATUS[t.lifecycle] == "active"
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content, "
        "event_range, recorded_range, layer, kind, lifecycle, retention, um_facet) "
        "VALUES (%s, %s, %s, %s, 'user', 'statement', false, 'x', tstzrange(%s, NULL), "
        "CASE WHEN %s THEN tstzrange(clock_now(), NULL) END, %s, %s, %s, %s, %s)",
        (
            tid,
            tid,
            user,
            STATUS[t.lifecycle],
            START,
            believed,
            LAYER[t.kind],
            t.kind,
            t.lifecycle,
            t.retention,
            t.facet,
        ),
    )
    if t.lifecycle != "retired":  # a staged (retired) row owes no record
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            (tid, "t-" + user, user),
        )
    if t.lifecycle in ("asserted", "contested"):  # the gate decision that admitted it (migration 14)
        await conn.execute(
            "INSERT INTO memory_conflict (id, candidate_version_id, user_id, relation, decision, reason_code, "
            "decided_by, decided_at) VALUES (%s, %s, %s, 'unrelated', %s, 'gate_passed', 'validation-gate', clock_now())",
            ("mc-" + tid, tid, user, "accept" if t.lifecycle == "asserted" else "hold"),
        )
    if (
        t.lifecycle == "inferred"
    ):  # I1': a derivation row and a registered method (v1 persona rows carry both implicitly)
        await conn.execute("INSERT INTO memory_derivation VALUES (%s, 'base', %s, 'consolidation')", (tid, user))
        if t.kind != "abstraction":
            await conn.execute("UPDATE memory_version SET method_id = 'm-v1-consolidation' WHERE id = %s", (tid,))


async def seed_base(conn: AsyncConnection) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
        await insert_tuple(conn, "base", Tuple4("claim", "durable", "asserted", None))


@pytest.mark.parametrize("t", LEGAL, ids=str)
async def test_every_legal_tuple_is_storable_and_queryable_and_deletable_and_routed(database, t: Tuple4) -> None:  # noqa: F811
    """Four properties, four assertions: a failure names the property and the tuple."""
    async with env(database) as (conn, schema, role, user):
        await provision_runtime_role(conn, schema, role)
        await seed_base(conn)
        # -- storable: the database accepts it as the restricted role
        async with tenant_scope(conn, user, role=role):
            await insert_tuple(conn, "subject", t)
            await insert_tuple_dependent(conn, user)
        stored = await rows(
            conn, "SELECT kind, retention, lifecycle, um_facet FROM memory_version WHERE id = 'subject'"
        )
        assert stored == [(t.kind, t.retention, t.lifecycle, t.facet)], "storable"
        # -- queryable: readable through row-level security, and a believed tuple is in the temporal current state
        async with tenant_scope(conn, user, role=role):
            visible = await rows(conn, "SELECT id, kind, lifecycle FROM memory_version WHERE id = 'subject'")
            believed = {
                version.id
                for version in (await fetch_versions(conn, current_state(user, NOW), schema_name=schema)).versions
            }
        assert [row[0] for row in visible] == ["subject"], "queryable (row-level security)"
        assert ("subject" in believed) == (STATUS[t.lifecycle] == "active"), "queryable (temporal current_state)"
        # -- routed: the row as read back (not the test's own tuple) goes where the independent reference model says
        kind, lifecycle = cast(Kind, visible[0][1]), cast(Lifecycle, visible[0][2])
        expected_channel = reference_channel(Item(t.kind, t.lifecycle, verification_of(t.lifecycle), False, False))
        assert channel_of(kind, lifecycle, verification_of(lifecycle), False, False) == expected_channel, "routed"
        # -- deletable: the row and its derived dependent are gone, nothing else is
        async with tenant_scope(conn, user, role=role):
            receipt = await delete_memory(conn, user, scope="memory", target_ref="subject", schema_name=schema)
        assert receipt.deleted_versions >= 1, "deletable"
        remaining = {r[0] for r in await rows(conn, "SELECT id FROM memory_version")}
        assert remaining == {"base"}, "deletable (closure: the dependent goes with its only source)"
        assert await rows(
            conn, "SELECT count(*) FROM memory_evidence WHERE version_id IN ('subject', 'dependent')"
        ) == [(0,)]


async def insert_tuple_dependent(conn: AsyncConnection, user: str) -> None:
    """A claim whose only source is ``subject``: deleting the subject must take it along (the deletion closure)."""
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content, "
        "event_range, recorded_range, layer) VALUES ('dependent', 'dependent', %s, 'active', 'user', 'statement', false, "
        "'summary', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic')",
        (user, START),
    )
    await conn.execute("INSERT INTO memory_derivation VALUES ('dependent', 'subject', %s, 'summary')", (user,))


# ------------------------------------------------------------------------------------------------- illegal tuples


@pytest.mark.parametrize("t", ILLEGAL, ids=str)
async def test_every_illegal_tuple_is_refused_by_the_database(database, t: Tuple4) -> None:  # noqa: F811
    async with env(database) as (conn, schema, role, user):
        await provision_runtime_role(conn, schema, role)
        await seed_base(conn)
        with pytest.raises((CheckViolation, RaiseException)) as refused:
            async with tenant_scope(conn, user, role=role):
                await insert_tuple(conn, "subject", t)
        text = str(refused.value)
        assert any(
            code in text
            for code in ("retention_floor", "lifecycle_legal_for_kind", "um_facet_check", "lifecycle_not_legal")
        ), text
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE id = 'subject'") == [(0,)]


def test_the_illegal_space_contains_every_retention_floor_violation_and_the_forbidden_facet() -> None:
    floor = {
        (t.kind, t.retention) for t in ILLEGAL if t.kind not in SHORT_RETENTION_KINDS and t.retention != "durable"
    }
    assert floor == {(k, r) for k in ("claim", "abstraction") for r in ("working", "session")}  # all four violations


async def test_a_seventh_facet_and_inferred_preference_are_refused_and_the_sql_set_is_the_model_set(database) -> None:  # noqa: F811
    async with env(database) as (conn, schema, role, user):
        await provision_runtime_role(conn, schema, role)
        await seed_base(conn)
        for facet in ("inferred_preference", "seventh"):
            with pytest.raises(CheckViolation, match="um_facet_check"):
                async with tenant_scope(conn, user, role=role):
                    await insert_tuple(conn, "subject", Tuple4("claim", "durable", "asserted", facet))
        definition = (
            await rows(
                conn,
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'memory_version_um_facet_check' AND conrelid = 'memory_version'::regclass",
            )
        )[0][0]
        quoted = {part.split("'")[1] for part in str(definition).split("::text") if "'" in part}
        assert quoted == set(FACETS)  # a seventh value added to the database fails here


# ------------------------------------------------------------------------------------------------- six memory types


def profile_with(name: str, *, kinds: list[str], retention: dict[str, str]) -> Profile:
    """A profile derived from the packaged document by CONFIGURATION ONLY (kinds and retention defaults)."""
    document = json.loads(conversational().model_dump_json())
    document.update(name=name, kinds=kinds, description=f"{name}: a configuration of the same engine")
    document["retention"] = {"defaults": retention, "promote_to_durable": False, "status": "decided"}
    return parse_profile(document)


async def episode_through_the_api(conn, engine: Engine, *, key: str) -> Receipt:
    receipt = await engine.call(
        caller("alice"),
        "ingest_episode",
        {
            "scope": "/u/alice/",
            "source": {
                "type": "turn",
                "ref": "said",
                "span": "vegetarian",
                "content_hash": canonical_hash("I am vegetarian"),
            },
            "content": "alice said she is vegetarian",
            "event_time": None,
            "idempotency_key": key,
        },
    )
    assert isinstance(receipt, Receipt)
    return receipt


async def add_turn(conn: AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES ('said', 's-alice', 'alice', 2, 'user', 'I am vegetarian', %s)",
        (START,),
    )


@pytest.mark.parametrize(
    ("memory_type", "retention", "expected"),
    [
        ("working memory", "working", ("episode", "working", "observed")),
        ("short-term memory", "session", ("episode", "session", "observed")),
        ("episodic memory", "durable", ("episode", "durable", "observed")),
        ("long-term memory", "durable", ("episode", "durable", "observed")),
    ],
)
async def test_four_of_the_six_types_are_a_profile_document_and_the_generic_engine_call(  # noqa: F811
    database, memory_type: str, retention: str, expected: tuple[str, str, str]
) -> None:
    """Working, short-term, episodic and long-term memory differ ONLY in the profile's retention default."""
    async with world(database) as (conn, engine, _schema):
        await add_turn(conn)
        profile = profile_with(memory_type.replace(" ", "-"), kinds=["episode"], retention={"episode": retention})
        register_ingest_handlers(engine, engine.resolver.runtime, profile)  # type: ignore[attr-defined]
        receipt = await episode_through_the_api(conn, engine, key="k")
        assert await rows(
            conn, "SELECT kind, retention, lifecycle FROM memory_version WHERE id = %s", (receipt.id,)
        ) == [expected]


async def test_semantic_memory_is_a_claim_that_is_observed_or_asserted_and_comes_from_the_same_call(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        await add_turn(conn)
        profile = profile_with("semantic", kinds=["claim"], retention={"claim": "durable"})
        register_ingest_handlers(engine, engine.resolver.runtime, profile)  # type: ignore[attr-defined]
        receipt = await engine.call(
            caller("alice"),
            "assert",
            {
                "scope": "/u/alice/",
                "kind": "claim",
                "content": "The user is vegetarian",
                "evidence": [
                    {
                        "type": "turn",
                        "ref": "said",
                        "span": "I am vegetarian",
                        "content_hash": canonical_hash("I am vegetarian"),
                    }
                ],
                "event_range": None,
                "idempotency_key": "s1",
            },
        )
        assert isinstance(receipt, Receipt)
        (kind, lifecycle) = (
            await rows(conn, "SELECT kind, lifecycle FROM memory_version WHERE id = %s", (receipt.id,))
        )[0]
        assert kind == "claim" and lifecycle in (
            "observed",
            "asserted",
        )  # the taxonomy mapping of ARCHITECTURE_V2 section 1
        # and a profile that does not enable the kind refuses it: configuration decides, with a static code
        episodes_only = profile_with("episodes-only", kinds=["episode"], retention={"episode": "durable"})
        register_ingest_handlers(engine, engine.resolver.runtime, episodes_only)  # type: ignore[attr-defined]
        with pytest.raises(Exception, match="validation_error"):
            await engine.call(
                caller("alice"),
                "assert",
                {
                    "scope": "/u/alice/",
                    "kind": "claim",
                    "content": "again",
                    "evidence": [
                        {
                            "type": "turn",
                            "ref": "said",
                            "span": "I am vegetarian",
                            "content_hash": canonical_hash("I am vegetarian"),
                        }
                    ],
                    "event_range": None,
                    "idempotency_key": "s2",
                },
            )


async def test_procedural_memory_is_the_procedure_kind_storable_under_every_retention_and_routed_to_procedures(
    database,
) -> None:  # noqa: F811
    """The storage-layer half: every lifecycle of a procedure is legal. The entry-point half follows (V2-D62 item 3)."""
    async with env(database) as (conn, schema, role, user):
        await provision_runtime_role(conn, schema, role)
        await seed_base(conn)
        for retention in RETENTIONS:
            async with tenant_scope(conn, user, role=role):
                await insert_tuple(conn, f"proc-{retention}", Tuple4("procedure", retention, "asserted", None))
        assert (
            await rows(conn, "SELECT kind, lifecycle FROM memory_version WHERE kind = 'procedure' ORDER BY retention")
            == [("procedure", "asserted")] * 3
        )
        assert channel_of("procedure", "asserted", "none", False, False) == "procedures"
        assert [t.lifecycle for t in LEGAL if t.kind == "procedure" and t.retention == "working" and t.facet is None] == [
            "asserted", "inferred", "contested", "retired"
        ]  # fmt: skip


@pytest.mark.parametrize("retention", RETENTIONS)
async def test_procedural_memory_is_created_and_read_back_through_engine_call(database, retention: str) -> None:  # noqa: F811
    """V2-D62 deferred item 3: ``assert`` (kind ``procedure``) creates it, ``get``/``query`` route it to ``procedures``."""
    async with world(database) as (conn, engine, _schema):
        await add_turn(conn)
        profile = profile_with("procedural", kinds=["procedure"], retention={"procedure": retention})
        register_ingest_handlers(engine, engine.resolver.runtime, profile)  # type: ignore[attr-defined]
        register_read_handlers(engine, engine.resolver.runtime)  # type: ignore[attr-defined]
        receipt = await engine.call(
            caller("alice"),
            "assert",
            {
                "scope": "/u/alice/",
                "kind": "procedure",
                "content": "Cook without meat",
                "evidence": [
                    {
                        "type": "turn",
                        "ref": "said",
                        "span": "I am vegetarian",
                        "content_hash": canonical_hash("I am vegetarian"),
                    }
                ],
                "event_range": None,
                "idempotency_key": f"p-{retention}",
            },
        )
        assert isinstance(receipt, Receipt)
        assert await rows(
            conn, "SELECT kind, retention, lifecycle FROM memory_version WHERE id = %s", (receipt.id,)
        ) == [("procedure", retention, "asserted")]
        got = await engine.call(caller("alice"), "get", {"artifact_id": receipt.id})
        assert isinstance(got, Artifact) and got.channel == "procedures"
        found = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(found, QueryResult) and [a.artifact_id for a in found.procedures] == [receipt.id]


async def test_create_scope_creates_a_usable_scope_through_the_entry_point_only(database) -> None:  # noqa: F811
    """G1 / E-040: scope creation is configuration, verified through `Engine.call` and never against a table.

    Migration 15 (`V2-D64`) is what makes this meaningful: before it a created sub-scope could hold no row, so the
    operation would have recorded a scope nothing could use.
    """
    async with world(database) as (conn, engine, _schema):
        created = await engine.call(
            caller("alice"),
            "create_scope",
            {"parent": "/u/alice/", "segment": "repo", "kind": "project", "idempotency_key": "sc"},
        )
        assert created == "/u/alice/repo/"
        assert "create_scope" not in engine.unwired_operations()
        await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
        await conn.execute(
            "INSERT INTO session (id, user_id, started_at, scope_path) VALUES ('s-scope', 'alice', %s, '/u/alice/repo/')",
            (START,),
        )
        assert await rows(conn, "SELECT scope_path FROM session WHERE id = 's-scope'") == [("/u/alice/repo/",)]


async def test_link_entity_is_wired_through_engine_call_and_both_endpoints_must_be_readable(database) -> None:  # noqa: F811
    """G1 / E-024 (#139 row 12): ``link_entity`` exercised only through ``Engine.call``, never against the table.

    Two scopes, one entity in each, then an explicit link: idempotent in either order, refused for a caller whose read
    set lacks one endpoint exactly as for an id that does not exist (T-09), and refused for another tenant's caller.
    """
    async with world(database) as (conn, engine, _schema):
        del conn
        scopes = [
            await engine.call(
                caller("alice"),
                "create_scope",
                {"parent": "/u/alice/", "segment": segment, "kind": "project", "idempotency_key": f"lk-{segment}"},
            )
            for segment in ("work", "home")
        ]
        entities = [
            await engine.call(
                caller("alice"),
                "create_entity",
                {"scope": scope, "entity_type": "person", "label": "Ada", "idempotency_key": f"lk-e-{scope}"},
            )
            for scope in scopes
        ]
        first, second = (str(entity) for entity in entities)
        # V2 §5 rule 4: a `private` row matches only a selector equal to its own scope, so a caller reading both
        # scopes names both exactly. The link needs both in its read set, which is what `targets_readable` checks.
        both = CallerContext.from_credential(
            Credential("alice", "C"),
            write_scope="/u/alice/",
            read_set=resolve_read_set("alice", [Selector.exact(str(scope)) for scope in scopes], []),
        )
        link = await engine.call(
            both, "link_entity", {"entity_a": first, "entity_b": second, "relation": "same_person"}
        )
        assert isinstance(link, Receipt) and link.effect == "linked"
        reverse = await engine.call(
            both, "link_entity", {"entity_a": second, "entity_b": first, "relation": "same_person"}
        )
        assert isinstance(reverse, Receipt) and reverse.id == link.id  # one link, whichever way it is asked
        one_scope = CallerContext.from_credential(
            Credential("alice", "C"),
            write_scope="/u/alice/",
            read_set=resolve_read_set("alice", [Selector.exact(str(scopes[0]))], []),
        )
        answers = []
        for other in (second, "ent_does_not_exist"):  # outside the read set, and absent: one answer
            with pytest.raises(EntryError) as refused:
                await engine.call(one_scope, "link_entity", {"entity_a": first, "entity_b": other, "relation": "x"})
            answers.append((refused.value.code, refused.value.detail))
        assert answers == [("forbidden", None), ("forbidden", None)]
        with pytest.raises(EntryError) as foreign:
            await engine.call(
                caller("bob"), "link_entity", {"entity_a": first, "entity_b": second, "relation": "same_person"}
            )
        assert (foreign.value.code, foreign.value.detail) == ("forbidden", None)


def test_no_profile_specific_code_path_in_the_generic_engine_api() -> None:
    """Requirement 6: the handlers read a profile's DATA (kinds, retention); they never branch on which profile it is."""
    root = Path(__file__).resolve().parents[2] / "src" / "custom_memory_engine"
    for relative in ("api/ingest_handlers.py", "api/handlers.py", "api/entry.py", "api/runtime.py"):
        assert find_profile_branching((root / relative).read_text(encoding="utf-8")) == [], relative


async def test_register_method_is_wired_through_engine_call(database) -> None:  # noqa: F811
    """I3 enforcement via the entry point: ``register_method`` is admin-only, idempotent on the same hash,
    and wired through Engine.call so the contract's static errors govern every call.

    This test drives the entry point only; the append-only and uniqueness guarantees are
    in ``test_migration_m6.py::test_method_rejects_update_delete_and_truncate_and_keeps_name_version_unique``.
    """
    digest = "a" * 64
    args: dict[str, object] = {
        "name": "e018-method",
        "version": "1",
        "params_schema_hash": digest,
        "description": "registered in test_type_completeness",
        "idempotency_key": "e018-m1",
    }
    async with world(database) as (conn, engine, _schema):
        del conn
        # non-admin callers are forbidden regardless of tier
        for ctx in (caller("alice"), caller("alice", "B", admin=True)):
            with pytest.raises(Exception, match="forbidden"):
                await engine.call(ctx, "register_method", args)
        # a Tier C admin caller succeeds and the result is a method id
        first = await engine.call(caller("alice", admin=True), "register_method", args)
        assert isinstance(first, str) and first.startswith("m_")
        # the same call replays: same hash → same id
        assert await engine.call(caller("alice", admin=True), "register_method", args) == first
        # a different hash for the same (name, version) is precondition_failed
        with pytest.raises(Exception, match="precondition_failed"):
            await engine.call(
                caller("alice", admin=True),
                "register_method",
                {**args, "params_schema_hash": "b" * 64, "idempotency_key": "e018-m2"},
            )


async def test_unwired_operations_are_reported_as_not_yet_testable_never_as_passes(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        del conn
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime)
        register_read_handlers(engine, runtime)
        register_infer_handlers(engine, runtime, tau_confidence=0.90, tau_coverage=0.60)  # V2-D30 suggested values
        unwired = set(engine.unwired_operations())
        assert {"delete", "export"} <= unwired  # W3+ owners: E-037, A-015
        # query/get (E-033 minimal) and infer (C-021 minimal) are wired under V2-D66 and tested through Engine.call
        assert not {"query", "get", "infer"} & unwired
        # `create_scope` left this list when migration 15 let a sub-scope hold a row (E-040, V2-D64).
        assert "create_scope" not in unwired
        # E-024 wires `link_entity` (V2-D71); `test_link_entity_is_wired_through_engine_call_...` drives it here.
        assert "link_entity" not in unwired
        print(
            f"\nG1: {len(OPERATIONS) - len(unwired)} of {len(OPERATIONS)} operations wired; "
            f"unwired (not yet testable): {sorted(unwired)}"
        )
