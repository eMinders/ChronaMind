"""E-026: ``purge_working`` through :meth:`Engine.call`, and the proof that a purge never removes answerability.

``V2-D10`` separates retention from belief; ``I10`` says a purge of working memory is not a deletion of knowledge. What
a purge does to an artifact whose evidence is a working episode is rule PW-1 (``engine/purge.py``): an episode a
surviving row cites as evidence is retained, every other working episode of the scope's subtree is deleted, and no
other row changes. The two shapes a durable artifact can rest on a working episode resolve differently:

* **grounded (fact)**: ``assert`` grounds a claim on the *source* the episode recorded (a turn). The episode is
  purged; the claim, its evidence and its source are untouched;
* **inferred (hypothesis)**: ``infer`` derives an abstraction *from* the episode rows. Those episodes are retained,
  so the abstraction keeps its derivation (``I1'``) and stays reachable by the deletion closure (``V2-D09``).

The destructive control replaces the rule, inside this module only, with one that follows the closure, and shows that
the answerability assertion then fails. Every call runs as a ``NOSUPERUSER NOBYPASSRLS`` role; the database is seeded
and inspected by the owner.
"""

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import fields

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.contract import Tier
from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.infer_handlers import register_infer_handlers
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.maintain_handlers import register_maintain_handlers
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.engine import purge
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.purge import PURGE_EVENT, PurgePlan, PurgeReceipt
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.profile import Profile, parse_profile
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import tenant_scope
from tests.integration.test_api_wiring_structure import (
    T0,
    database,  # noqa: F401  (fixture)
    rows,
    world,
)
from tests.integration.test_infer_db import infer_args, method
from tests.integration.test_read_path_db import CODING_PROFILE, episode

pytestmark = pytest.mark.integration

REPO = "/u/alice/p/chrona/"
SESSION = REPO + "s/s1/"
BOB_SESSION = "/u/bob/p/chrona/s/s1/"
TEXT = "I am vegetarian and I do not eat fish"
TURN = {"type": "turn", "ref": "said", "span": TEXT, "content_hash": canonical_hash(TEXT)}


def working_coding_profile() -> Profile:
    """The coding profile with working episodes and procedures: configuration only (``V2-D14`` defaults stay open)."""
    document = json.loads(CODING_PROFILE.model_dump_json())
    document["name"] = "coding-working-memory"
    document["retention"]["defaults"].update(episode="working", procedure="working")
    return parse_profile(document)


def ctx(user: str, tier: Tier = "C", *, admin: bool = False) -> CallerContext:
    """A caller that selected its root, the repository and the session.

    V2 §5 rule 4: a ``private`` row is read only through a selector equal to its scope.
    """
    root = f"/u/{user}/"
    scopes = (root, root + "p/chrona/", root + "p/chrona/s/s1/")
    read_set = resolve_read_set(user, [Selector.of(scope) for scope in scopes], [])
    return CallerContext.from_credential(Credential(user, tier, admin), write_scope=root, read_set=read_set)


def admin(user: str = "alice") -> CallerContext:
    return ctx(user, admin=True)


class Workspace:
    """One coding session's memory: what a purge deletes, what it keeps, and the artifacts it must not touch."""

    fact_episode: str  # working episode recorded from turn `said` (purged)
    loose_episode: str  # working tool episode nothing cites (purged)
    cited_episodes: tuple[str, str]  # working tool episodes an abstraction derives from (retained as evidence)
    repo_episode: str  # working episode at the repository level, outside the session (untouched by a session purge)
    session_episode: str  # a `session`-retention episode in the session (never purged: not working)
    procedure: str  # a working procedure in the session (never purged: not an episode)
    claim: str  # the durable claim grounded on turn `said` (fact)
    abstraction: str  # the durable abstraction inferred from `cited_episodes` (hypothesis)
    bob_episode: str  # bob's working episode at the same relative path (isolation)


async def _receipt(engine: Engine, caller: CallerContext, operation: str, arguments: dict[str, object]) -> str:
    receipt = await engine.call(caller, operation, arguments)
    assert isinstance(receipt, Receipt), receipt
    return receipt.id


@asynccontextmanager
async def workspace(database) -> AsyncIterator[tuple[AsyncConnection, Engine, Workspace]]:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        for user in ("alice", "bob"):
            await conn.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES (%s, %s, %s, 2, 'user', %s, %s)",
                ("said" if user == "alice" else "bob-said", "s-" + user, user, TEXT, T0),
            )
        register_read_handlers(engine, runtime)
        register_maintain_handlers(engine, runtime)
        profile = working_coding_profile()
        register_infer_handlers(engine, runtime, tau_confidence=0.90, tau_coverage=0.60, profile=profile)
        w = Workspace()

        # a `session` episode first, under the unchanged coding profile (episodes default to `session`)
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        w.session_episode = await _receipt(engine, ctx("alice"), "ingest_episode", episode(SESSION, "kept", "notes"))

        register_ingest_handlers(engine, runtime, profile)
        alice = ctx("alice")
        w.fact_episode = await _receipt(
            engine,
            alice,
            "ingest_episode",
            {"scope": SESSION, "source": TURN, "content": TEXT, "event_time": T0.isoformat(), "idempotency_key": "f"},
        )
        w.loose_episode = await _receipt(engine, alice, "ingest_episode", episode(SESSION, "loose", "pytest -x ran"))
        w.cited_episodes = (
            await _receipt(engine, alice, "ingest_episode", episode(SESSION, "c1", "run one: NameError: json")),
            await _receipt(engine, alice, "ingest_episode", episode(SESSION, "c2", "run two: NameError: json")),
        )
        w.repo_episode = await _receipt(engine, alice, "ingest_episode", episode(REPO, "repo", "git status clean"))
        w.claim = await _receipt(
            engine,
            alice,
            "assert",
            {
                "scope": REPO,
                "kind": "claim",
                "content": "The user is vegetarian",
                "evidence": [{**TURN, "span": "I am vegetarian"}],
                "event_range": None,
                "idempotency_key": "claim",
            },
        )
        w.procedure = await _receipt(
            engine,
            alice,
            "assert",
            {
                "scope": SESSION,
                "kind": "procedure",
                "content": "Offer a vegetarian option first",
                "evidence": [{**TURN, "span": "I do not eat fish"}],
                "event_range": None,
                "idempotency_key": "procedure",
            },
        )
        ref = await method(engine)
        w.abstraction = await _receipt(
            engine, ctx("alice", "B"), "infer", infer_args(ref, list(w.cited_episodes), scope=REPO)
        )
        w.bob_episode = await _receipt(engine, ctx("bob"), "ingest_episode", episode(BOB_SESSION, "b", "bob's run"))
        yield conn, engine, w


async def purge_scope(engine: Engine, scope: str, caller: CallerContext | None = None) -> PurgeReceipt:
    receipt = await engine.call(caller or admin(), "purge_working", {"scope": scope})
    assert isinstance(receipt, PurgeReceipt)
    return receipt


async def snapshot(conn: AsyncConnection) -> dict[str, set[str]]:
    """Every row of every engine table, as the owner sees it."""
    tables = [
        str(row[0])
        for row in await rows(
            conn,
            "SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') ORDER BY 1",
        )
    ]
    taken: dict[str, set[str]] = {}
    for table in tables:
        found = await rows(conn, f'SELECT to_jsonb(t)::text FROM "{table}" AS t')  # noqa: S608 (catalog names)
        taken[table] = {str(row[0]) for row in found}
    return taken


def changed(before: dict[str, set[str]], after: dict[str, set[str]]) -> dict[str, tuple[list[dict], list[dict]]]:
    """Per table: (rows removed, rows added). Unchanged tables are omitted."""
    diff: dict[str, tuple[list[dict], list[dict]]] = {}
    for table in sorted(before.keys() | after.keys()):
        removed = [json.loads(r) for r in sorted(before.get(table, set()) - after.get(table, set()))]
        added = [json.loads(r) for r in sorted(after.get(table, set()) - before.get(table, set()))]
        if removed or added:
            diff[table] = (removed, added)
    return diff


async def query(engine: Engine, scope: str) -> QueryResult:
    result = await engine.call(ctx("alice"), "query", {"request": {"scope": scope}})
    assert isinstance(result, QueryResult)
    return result


async def temporal_query(engine: Engine, mode: str, scope: str = REPO, **extra: object) -> set[str]:
    """The ids one temporal mode returns to alice, **through the public entry point** (``E-031``).

    This is the whole point of ``E-026`` §9 rows 5 and 13: answerability must hold for the operation a caller can
    actually invoke, not only for a predicate called below the entry point. ``E-031`` wired the modes, so the
    assertion no longer needs an in-process fallback.
    """
    request: dict[str, object] = {"scope": scope, "mode": mode, **extra}
    result = await engine.call(ctx("alice"), "query", {"request": request})
    assert isinstance(result, QueryResult), result
    return {a.artifact_id for a in (*result.facts, *result.hypotheses, *result.procedures, *result.conflicts)}


async def assert_answerable(conn: AsyncConnection, engine: Engine, w: Workspace) -> None:
    """``I10``: both durable artifacts are returned, routed to their channel, and carry their provenance record.

    Everything here goes through ``Engine.call``: ``query`` in each temporal mode, ``get`` of each id, and the
    evidence / derivation records the ``provenance`` operation will render. Nothing is asserted below the entry
    point, so a passing run proves what a caller can observe (``E-026`` §9 rows 5, 13).
    """
    result = await query(engine, REPO)
    assert w.claim in {a.artifact_id for a in result.facts}, "the grounded claim left `facts`"
    assert w.abstraction in {a.artifact_id for a in result.hypotheses}, "the inference left `hypotheses`"
    for artifact_id, channel in ((w.claim, "facts"), (w.abstraction, "hypotheses")):
        artifact = await engine.call(ctx("alice"), "get", {"artifact_id": artifact_id})
        assert isinstance(artifact, Artifact) and artifact.channel == channel, f"{channel} artifact is not answerable"
    assert {w.claim, w.abstraction} <= await temporal_query(engine, "current_state"), (
        "current_state lost a durable artifact"
    )
    assert {w.claim, w.abstraction} <= await temporal_query(engine, "historical_as_of", target_time=T0.isoformat()), (
        "historical_as_of lost a durable artifact"
    )
    # provenance: the claim still cites the turn it was grounded on, the inference still derives from its episodes
    assert await rows(
        conn, "SELECT source_type, source_ref FROM memory_evidence WHERE version_id = %s", (w.claim,)
    ) == [("turn", "said")]
    assert await rows(conn, "SELECT 1 FROM turn WHERE id = 'said'") == [(1,)]
    derived_from = await rows(
        conn,
        "SELECT d.source_version_id FROM memory_derivation d JOIN memory_version v ON v.id = d.source_version_id "
        "WHERE d.derived_version_id = %s ORDER BY 1",
        (w.abstraction,),
    )
    assert [row[0] for row in derived_from] == sorted(w.cited_episodes), "the inference lost its derivation (I1')"


async def ids_of(conn: AsyncConnection, ids: Iterable[str]) -> set[str]:
    return {str(row[0]) for row in await rows(conn, "SELECT id FROM memory_version WHERE id = ANY(%s)", (list(ids),))}


# ---------------------------------------------------------------------------------------------- wiring and tiers


async def test_purge_working_is_wired_and_refused_to_every_caller_but_an_admin_in_its_own_namespace(database) -> None:  # noqa: F811
    async with workspace(database) as (conn, engine, _w):
        assert "purge_working" not in engine.unwired_operations()
        before = await snapshot(conn)
        refusals = {
            "tier_b": (ctx("alice", "B"), SESSION),
            "tier_b_claiming_admin": (ctx("alice", "B", admin=True), SESSION),
            "tier_c_not_admin": (ctx("alice"), SESSION),
            "admin_outside_own_namespace": (admin(), BOB_SESSION),
            "admin_on_another_users_root": (admin("bob"), "/u/alice/"),
        }
        for label, (caller, scope) in refusals.items():
            with pytest.raises(EntryError) as refused:
                await engine.call(caller, "purge_working", {"scope": scope})
            assert refused.value.code == "forbidden", label
            assert str(refused.value) == "forbidden"  # static: no scope, path or count leaks
        for bad in ({}, {"scope": "not-a-path"}, {"scope": SESSION, "as_of": None}):
            with pytest.raises(EntryError) as invalid:
                await engine.call(admin(), "purge_working", bad)
            assert invalid.value.code in {"forbidden", "validation_error"}
        assert changed(before, await snapshot(conn)) == {}, "a refused purge changed something"


# ---------------------------------------------------------------------------------------------- bounded effect


async def test_a_session_purge_deletes_only_the_unreferenced_working_episodes_of_that_scope(database) -> None:  # noqa: F811
    async with workspace(database) as (conn, engine, w):
        before = await snapshot(conn)
        receipt = await purge_scope(engine, SESSION)
        assert (receipt.purged_episodes, receipt.retained_as_evidence) == (2, 2)
        diff = changed(before, await snapshot(conn))
        # Exactly three tables change: the two episode rows and their evidence rows go, one scope event arrives.
        assert set(diff) == {"memory_version", "memory_evidence", "audit_event"}, sorted(diff)
        removed_versions, added_versions = diff["memory_version"]
        assert {row["id"] for row in removed_versions} == {w.fact_episode, w.loose_episode}
        assert all(row["kind"] == "episode" and row["retention"] == "working" for row in removed_versions)
        assert added_versions == []  # no surviving row was rewritten (a rewrite would show as removed + added)
        removed_evidence, added_evidence = diff["memory_evidence"]
        assert {row["version_id"] for row in removed_evidence} == {w.fact_episode, w.loose_episode}
        assert added_evidence == []
        assert [row["event_type"] for row in diff["audit_event"][1]] == [PURGE_EVENT]
        assert diff["audit_event"][0] == []
        # what stayed, by name: evidence-bearing, repository-level, non-working, non-episode, and bob's
        survivors = [*w.cited_episodes, w.repo_episode, w.session_episode, w.procedure, w.claim, w.abstraction]
        assert await ids_of(conn, [*survivors, w.bob_episode]) == {*survivors, w.bob_episode}
        assert await rows(
            conn, "SELECT retention FROM memory_version WHERE id = ANY(%s)", (list(w.cited_episodes),)
        ) == [("working",), ("working",)]  # retained, not promoted (V2-D70 promotes `session` only)


async def test_a_repository_purge_reaches_the_subtree_and_never_another_tenant(database) -> None:  # noqa: F811
    async with workspace(database) as (conn, engine, w):
        await purge_scope(engine, SESSION)
        bob_before = await rows(conn, "SELECT to_jsonb(v)::text FROM memory_version v WHERE user_id = 'bob'")
        receipt = await purge_scope(engine, REPO)  # the session lies in the repository's subtree
        assert (receipt.purged_episodes, receipt.retained_as_evidence) == (1, 2)
        assert await ids_of(conn, [w.repo_episode]) == set()
        assert await rows(conn, "SELECT to_jsonb(v)::text FROM memory_version v WHERE user_id = 'bob'") == bob_before
        # alice's whole namespace: bob's working episode at the same relative path is still out of reach
        await purge_scope(engine, "/u/alice/")
        assert await ids_of(conn, [w.bob_episode]) == {w.bob_episode}
        # bob purges his own session: alice's retained evidence is untouched
        bob = await purge_scope(engine, BOB_SESSION, admin("bob"))
        assert (bob.purged_episodes, bob.retained_as_evidence) == (1, 0)
        assert await ids_of(conn, w.cited_episodes) == set(w.cited_episodes)


# ---------------------------------------------------------------------------------------------- receipt and audit


async def test_the_receipt_and_the_scope_event_are_content_free_and_a_repeat_purges_nothing(database) -> None:  # noqa: F811
    async with workspace(database) as (conn, engine, w):
        first = await purge_scope(engine, SESSION)
        again = await purge_scope(engine, SESSION)
        assert {f.name for f in fields(PurgeReceipt)} == {"purged_episodes", "retained_as_evidence", "recorded_at"}
        assert (again.purged_episodes, again.retained_as_evidence) == (0, 2)  # no error, no effect
        events = await rows(
            conn,
            # both events carry the engine clock, which the scenario pins, so order by what each one recorded
            "SELECT memory_ref, details FROM audit_event WHERE event_type = %s "
            "ORDER BY (details->>'purged_episodes')::int DESC",
            (PURGE_EVENT,),
        )
        assert [details for _, details in events] == [
            {"purged_episodes": 2, "retained_as_evidence": 2},
            {"purged_episodes": 0, "retained_as_evidence": 2},
        ]
        recorded = json.dumps([[ref, details] for ref, details in events]) + repr((first, again))
        for secret in (*vars(w).values(), *w.cited_episodes, SESSION, "chrona", TEXT, "vegetarian"):
            if isinstance(secret, str):
                assert secret not in recorded, "the receipt or the scope event names an id, a path or content"


# ---------------------------------------------------------------------------------------------- answerability (I10)


async def test_rule_pw1_a_purge_never_removes_answerability(database) -> None:  # noqa: F811
    """The §4 rule end to end: ingest → derive → purge → query. Both durable artifacts answer exactly as before."""
    async with workspace(database) as (conn, engine, w):
        await assert_answerable(conn, engine, w)
        await purge_scope(engine, SESSION)
        await assert_answerable(conn, engine, w)
        await purge_scope(engine, REPO)
        await assert_answerable(conn, engine, w)


async def test_a_purged_episode_is_absent_not_a_tombstone_and_answers_like_an_unknown_id(database) -> None:  # noqa: F811
    async with workspace(database) as (conn, engine, w):
        # a private row is read through a selector equal to its own scope (V2 §5 rule 4), so ask at SESSION
        before = await temporal_query(engine, "current_state", SESSION)
        as_of_before = await temporal_query(engine, "historical_as_of", SESSION, target_time=T0.isoformat())
        assert w.fact_episode in before and w.fact_episode in as_of_before
        await purge_scope(engine, SESSION)
        after = await temporal_query(engine, "current_state", SESSION)
        as_of_after = await temporal_query(engine, "historical_as_of", SESSION, target_time=T0.isoformat())
        assert w.fact_episode not in after | as_of_after
        assert w.fact_episode not in {a.artifact_id for a in (await query(engine, SESSION)).facts}
        for unknown in (w.fact_episode, "v_never_existed"):
            with pytest.raises(EntryError) as refused:
                await engine.call(ctx("alice"), "get", {"artifact_id": unknown})
            assert (refused.value.code, refused.value.detail) == ("forbidden", None)


async def test_retained_evidence_keeps_the_inference_reachable_by_the_deletion_closure(database) -> None:  # noqa: F811
    """Why PW-1 retains instead of dropping the pointer: a later lineage deletion must still reach the inference."""
    async with workspace(database) as (conn, engine, w):
        await purge_scope(engine, SESSION)
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        (memory_key,) = (
            await rows(conn, "SELECT memory_key FROM memory_version WHERE id = %s", (w.cited_episodes[0],))
        )[0]
        async with tenant_scope(conn, "alice", role=runtime.role):
            receipt = await delete_memory(
                conn, "alice", scope="memory", target_ref=str(memory_key), schema_name=runtime.schema_name
            )
        assert receipt.dependents == 1  # the closure walked from the retained episode to the abstraction


async def test_a_grounded_claim_never_points_at_the_episode_so_its_purge_cannot_reach_it(database) -> None:  # noqa: F811
    """The fact case: the claim and the purged episode share a *source*; neither row cites the other."""
    async with workspace(database) as (conn, engine, w):
        assert await rows(
            conn,
            "SELECT count(*) FROM memory_derivation WHERE %s IN (source_version_id, derived_version_id)",
            (w.fact_episode,),
        ) == [(0,)]
        await purge_scope(engine, SESSION)
        assert await ids_of(conn, [w.fact_episode, w.claim]) == {w.claim}
        assert await rows(conn, "SELECT lifecycle, retention FROM memory_version WHERE id = %s", (w.claim,)) == [
            ("asserted", "durable")
        ]


async def test_an_episode_that_opens_a_contradiction_is_retained_and_the_claim_stays_in_conflicts(database) -> None:  # noqa: F811
    """Review finding: an association cascades with its endpoint. A Tier C ``contradicts`` edge from a working episode
    routes the durable claim at its other end to ``conflicts``; purging the episode must not move it back to ``facts``.
    A plain ``related_to`` edge is relatedness, not evidence, and goes with the purged episode."""
    async with workspace(database) as (conn, engine, w):
        contradiction = await engine.call(
            ctx("alice"), "link", {"from": w.loose_episode, "to": w.claim, "type": "contradicts", "weight": 1}
        )
        related = await engine.call(
            ctx("alice"), "link", {"from": w.fact_episode, "to": w.claim, "type": "related_to", "weight": 1}
        )
        assert isinstance(contradiction, str) and isinstance(related, str)
        assert w.claim in {a.artifact_id for a in (await query(engine, REPO)).conflicts}
        before = await snapshot(conn)
        receipt = await purge_scope(engine, SESSION)
        assert (receipt.purged_episodes, receipt.retained_as_evidence) == (1, 3)
        diff = changed(before, await snapshot(conn))
        assert set(diff) == {"memory_version", "memory_evidence", "association", "audit_event"}, sorted(diff)
        assert [row["id"] for row in diff["memory_version"][0]] == [w.fact_episode]
        assert [row["id"] for row in diff["association"][0]] == [related] and diff["association"][1] == []
        assert w.claim in {a.artifact_id for a in (await query(engine, REPO)).conflicts}
        got = await engine.call(ctx("alice"), "get", {"artifact_id": w.claim})
        assert isinstance(got, Artifact) and got.channel == "conflicts"


# ---------------------------------------------------------------------------------------------- controls


def cascading_plan(candidates: list[str], citations: Iterable[tuple[str, str]]) -> PurgePlan:
    """The destructive control: purge follows the closure, deleting every row derived from a purged episode."""
    doomed = set(candidates)
    pairs = list(citations)
    grew = True
    while grew:
        before = len(doomed)
        doomed |= {citing for cited, citing in pairs if cited in doomed}
        grew = len(doomed) != before
    return PurgePlan(purged=tuple(sorted(doomed)), retained_as_evidence=())


async def test_the_destructive_control_makes_the_answerability_assertion_fail(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``I10``'s witness: the same assertion, under a purge that is a deletion, must fail. A green suite that never
    watched the invariant break is not evidence (E-026 W5)."""
    async with workspace(database) as (conn, engine, w):
        await assert_answerable(conn, engine, w)
        monkeypatch.setattr(purge, "plan_purge", cascading_plan)  # test-local: the product path never has a switch
        receipt = await purge_scope(engine, SESSION)
        assert (receipt.purged_episodes, receipt.retained_as_evidence) == (5, 0)  # four episodes and the inference
        with pytest.raises(AssertionError) as broken:
            await assert_answerable(conn, engine, w)
        print(f"\ndestructive control -> answerability assertion failed: {broken.value}")
        assert "hypotheses" in str(broken.value)


async def test_a_plan_that_ignored_its_citations_fails_closed_at_the_database(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second control: dropping the pointer is not available either. The derivation key is restrictive, so a
    plan that purged a cited episode is refused by the database and the whole purge rolls back."""
    async with workspace(database) as (conn, engine, w):
        monkeypatch.setattr(purge, "plan_purge", lambda candidates, _citations: PurgePlan(tuple(candidates), ()))
        before = await snapshot(conn)
        with pytest.raises(EntryError) as refused:
            await purge_scope(engine, SESSION)
        assert refused.value.code == "internal_error"
        assert changed(before, await snapshot(conn)) == {}
        await assert_answerable(conn, engine, w)


# ---------------------------------------------------------------------------------------------- the rule's inputs


async def test_every_non_cascading_reference_to_a_version_is_an_evidence_reference_or_named_here(database) -> None:  # noqa: F811
    """A new foreign key into ``memory_version`` must be classified before a purge can ignore it."""
    async with world(database) as (conn, _engine, _schema):
        found = await rows(
            conn,
            "SELECT c.conrelid::regclass::text, a.attname, c.confdeltype FROM pg_catalog.pg_constraint c "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "WHERE c.contype = 'f' AND c.confrelid = 'memory_version'::regclass AND c.confdeltype <> 'c'",
        )
        references = {(str(table).split(".")[-1], str(column)) for table, column, _ in found}
        cited = {(table, column) for table, column, _ in purge.EVIDENCE_REFERENCES}
        # a dissent names the inferred artifact the user rejected; an episode is never inferred, so never dissented
        not_evidence = {("negative_constraint", "dissented_version_id")}
        assert references == cited | not_evidence
