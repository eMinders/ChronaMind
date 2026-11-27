"""Owner-only re-filing through the host, including erasure of the second copy (T-50)."""

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from custom_memory_engine.api import move
from custom_memory_engine.api.entry import EntryError
from custom_memory_engine.api.read_handlers import Artifact
from custom_memory_engine.scope.access import Selector, resolve_read_set
from tests.integration.test_api_wiring_structure import caller, world
from tests.integration.test_host_e2e_db import call, connect, live, make_inferred
from tests.integration.test_migration_m1 import database, rows  # noqa: F401 (fixture)
from tests.integration.test_procedure_shape_db import FIX_CI, STEPS, get, store, wire
from tests.integration.test_visibility_sharing_db import claim_id, scope_reads

pytestmark = pytest.mark.integration
DEST = "/u/alice/project/"


def request(artifact: str, scope: str = DEST, key: str = "move-1") -> dict[str, object]:
    return {"artifact_id": artifact, "target_scope": scope, "idempotency_key": key}


async def test_move_replays_once_and_deleting_the_source_reaches_both_versions(database) -> None:  # noqa: F811
    async with live(database) as host, connect(database) as db:
        await make_inferred(db, "original", "both copies must disappear")
        responses = await asyncio.gather(
            *(call(host.client, host.alice_c, "move", **request("original")) for _ in range(2))
        )
        assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
        new = responses[0].json()["result"]
        assert new != "original" and new == responses[1].json()["result"]
        assert await rows(
            db,
            "SELECT id, scope_path, lifecycle, archival_branch FROM memory_version "
            "WHERE memory_key = 'original' ORDER BY scope_path",
        ) == [("original", "/u/alice/", "retired", "S"), (new, DEST, "inferred", None)]
        assert await rows(
            db,
            "SELECT upper(o.recorded_range) = lower(n.recorded_range), "
            "o.event_range = n.event_range, o.observed_at = n.observed_at, o.content = n.content, "
            "o.method_id = n.method_id, o.created_tier = n.created_tier FROM memory_version o, "
            "memory_version n WHERE o.id = 'original' AND n.id = %s",
            (new,),
        ) == [(True,) * 6]
        for version in ("original", new):
            assert await rows(
                db,
                "SELECT source_version_id, derivation_type FROM memory_derivation WHERE derived_version_id = %s",
                (version,),
            ) == [("src-original", "consolidation")]
        assert await rows(
            db,
            "SELECT a.from_id, a.to_id, a.type, a.scope_path, p.origin_kind FROM association a "
            "JOIN artifact_provenance p ON p.artifact_id = a.id WHERE a.to_id = 'original'",
        ) == [(new, "original", "supersedes", DEST, "tier_c_call")]
        again = await call(host.client, host.alice_c, "move", **request("original", key="different"))
        assert again.status_code == 412
        erased = await call(
            host.client, host.alice_c, "delete", scope="memory", target_ref="src-original", deletion_epoch=1
        )
        assert erased.status_code == 200, erased.text
        assert erased.json()["result"]["deleted_versions"] == 3
        assert await rows(db, "SELECT id FROM memory_version WHERE user_id = 'alice'") == []
        assert await rows(db, "SELECT id FROM association WHERE user_id = 'alice'") == []
        assert await rows(db, "SELECT id FROM artifact_provenance WHERE user_id = 'alice'") == []


async def test_only_the_owner_can_move_and_sharing_the_successor_uses_the_normal_read_audit(database) -> None:  # noqa: F811
    async with live(database) as host, connect(database) as db:
        original = await claim_id(host.client, host.alice_b, "My annotated travel plan", "claim")
        await call(host.client, host.alice_c, "grant_access", scope="/u/alice/", principal="bob", idempotency_key="g")
        await call(host.client, host.alice_c, "set_visibility", artifact_id=original, visibility="shared")
        granted = await call(host.client, host.bob_c, "get", artifact_id=original, read_scopes=["/u/alice/"])
        assert granted.status_code == 200 and granted.json()["result"]["content"] == "My annotated travel plan"
        for token, args in (
            (host.alice_b, request(original)),
            (host.alice_c, request(original, "/u/bob/")),
            (host.bob_c, {**request(original, "/u/bob/"), "read_scopes": ["/u/alice/"]}),
            (host.bob_c, request("missing", "/u/bob/")),
        ):
            denied = await call(host.client, token, "move", **args)
            assert denied.status_code == 403 and denied.json() == {"error": {"code": "forbidden"}}
        malformed = await call(host.client, host.alice_c, "move", **request(original, "/u/alice"))
        assert malformed.status_code == 400
        same = await call(host.client, host.alice_c, "move", **request(original, "/u/alice/"))
        assert same.status_code == 412
        before = await scope_reads(database, "bob")
        moved = await call(host.client, host.alice_c, "move", **request(original))
        assert moved.status_code == 200, moved.text
        new = moved.json()["result"]
        assert await scope_reads(database, "alice") == 0
        evidence = (
            "SELECT turn_id, evidence_span, evidence_role, source_type, source_ref, span, "
            "content_hash, authority_rank FROM memory_evidence WHERE version_id = %s ORDER BY source_ref, span"
        )
        original_citations = await rows(db, evidence, (original,))
        assert original_citations and await rows(db, evidence, (new,)) == original_citations
        read = {"artifact_id": new, "read_scopes": ["/u/alice/"]}
        hidden = await call(host.client, host.bob_b, "get", **read)
        assert hidden.status_code == 403 and await scope_reads(database, "bob") == before
        await call(host.client, host.alice_c, "set_visibility", artifact_id=new, visibility="shared")
        visible = await call(host.client, host.bob_b, "get", **read)
        assert visible.status_code == 200 and visible.json()["result"]["content"] == "My annotated travel plan"
        assert await scope_reads(database, "bob") == before + 1
        [(key,)] = await rows(db, "SELECT memory_key FROM memory_version WHERE id = %s", (new,))
        erased = await call(host.client, host.alice_c, "delete", scope="memory", target_ref=key, deletion_epoch=1)
        assert erased.status_code == 200 and erased.json()["result"]["deleted_versions"] == 2, erased.text
        assert await rows(db, "SELECT id FROM memory_version WHERE memory_key = %s", (key,)) == []


async def test_a_failure_after_retirement_rolls_back_the_entire_move_and_key(database, monkeypatch) -> None:  # noqa: F811
    async with live(database) as host, connect(database) as db:
        original = await claim_id(host.client, host.alice_b, "My ferry leaves tonight", "claim")

        async def fail(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("failure before commit")

        with monkeypatch.context() as patch:
            patch.setattr(move, "remember", fail)
            result = await call(host.client, host.alice_c, "move", **request(original))
            assert result.status_code == 500 and result.json() == {"error": {"code": "internal_error"}}
        assert await rows(
            db, "SELECT lifecycle, upper_inf(recorded_range) FROM memory_version WHERE id = %s", (original,)
        ) == [("asserted", True)]
        assert await rows(db, "SELECT id FROM memory_version WHERE scope_path = %s", (DEST,)) == []
        assert await rows(db, "SELECT id FROM association WHERE to_id = %s", (original,)) == []
        assert await rows(db, "SELECT result_id FROM api_idempotency WHERE operation = 'move'") == []
        retry = await call(host.client, host.alice_c, "move", **request(original))
        assert retry.status_code == 200, retry.text


async def test_a_moved_procedure_retains_its_steps_preconditions_and_outcome_history(database) -> None:  # noqa: F811
    async with world(database) as (db, engine, _schema):
        wire(engine)
        old = await store(
            engine, db, "proc", "Run the checks", sequence=2, shape={"preconditions": FIX_CI, "steps": STEPS}
        )
        await engine.call(caller("alice"), "record_outcome", {"procedure_id": old, "outcome": "success", "cost": 2})
        before = await get(engine, old)
        new = await engine.call(caller("alice"), "move", request(old))
        assert isinstance(new, str)
        ctx = replace(caller("alice"), read_set=resolve_read_set("alice", [Selector.of(DEST)], []))
        after = await engine.call(ctx, "get", {"artifact_id": new})
        assert isinstance(after, Artifact)
        assert after.steps == before.steps and after.steps
        assert after.preconditions == before.preconditions and after.preconditions
        assert await rows(
            db, "SELECT successes, failures, mean_cost FROM outcome_stats WHERE version_id = %s", (new,)
        ) == [(1, 0, 2.0)]


async def test_move_cannot_clear_a_contradiction_or_reintroduce_a_dissented_inference(database) -> None:  # noqa: F811
    async with live(database) as host, connect(database) as db:
        await make_inferred(db, "inference", "An inference in another scope")
        old = await claim_id(host.client, host.alice_b, "The office is open", "open")
        other = await claim_id(host.client, host.alice_b, "The office is closed", "closed")
        link = await call(host.client, host.alice_c, "link", **{"from": old, "to": other, "type": "contradicts"})
        assert link.status_code == 200, link.text
        denied = await call(host.client, host.alice_c, "move", **request(old))
        assert denied.status_code == 412 and denied.json()["error"]["detail"] == "move_blocked_by_conflict_or_dissent"
        await call(host.client, host.alice_c, "unlink", association_id=link.json()["result"])
        allowed = await call(host.client, host.alice_c, "move", **request(old))
        assert allowed.status_code == 200, allowed.text

        await db.execute(
            "INSERT INTO negative_constraint (id, user_id, scope_path, predicate_key, "
            "method_family, dissented_at, reason) SELECT 'veto', 'alice', %s, 'statement', "
            "name, clock_now(), 'user_dissent' FROM method WHERE id = 'm-v1-consolidation'",
            (DEST,),
        )
        denied = await call(host.client, host.alice_c, "move", **request("inference", key="veto"))
        assert denied.status_code == 412 and denied.json()["error"]["detail"] == "move_blocked_by_conflict_or_dissent"
        allowed = await call(host.client, host.alice_c, "move", **request("inference", "/u/alice/other/", "veto"))
        assert allowed.status_code == 200, allowed.text


async def test_scope_bound_artifacts_and_single_value_collisions_keep_the_original_intact(database) -> None:  # noqa: F811
    async with world(database) as (db, engine, _schema):
        person = await engine.call(
            caller("alice"),
            "create_entity",
            {"scope": "/u/alice/", "label": "A person", "entity_type": "person", "idempotency_key": "person"},
        )
        assert isinstance(person, str)
        async with db.transaction():
            # Seed a legacy persona, an entity subject, and the same exclusive slot in two scopes.
            for artifact, scope, layer, entity in (
                ("source", "/u/alice/", "semantic", None),
                ("occupied", DEST, "semantic", None),
                ("about", "/u/alice/", "semantic", person),
                ("abstraction", "/u/alice/", "persona", None),
            ):
                await db.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, "
                    "predicate_key, single_valued, content, event_range, recorded_range, layer, "
                    "scope_path, subject_entity_id) VALUES (%s, %s, 'alice', 'active', %s, 'diet', %s, "
                    "'x', tstzrange(clock_now(), NULL), tstzrange(clock_now(), NULL), %s, %s, %s)",
                    (artifact, artifact, entity or "self", artifact in ("source", "occupied"), layer, scope, entity),
                )
                await db.execute(
                    "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                    "VALUES (%s, 't-alice', 'alice', 'x', 'user')",
                    (artifact,),
                )
        for artifact, reason in (
            ("about", "scope_bound_artifact"),
            ("abstraction", "scope_bound_artifact"),
            ("source", "target_scope_has_conflicting_belief"),
        ):
            with pytest.raises(EntryError) as refused:
                await engine.call(caller("alice"), "move", request(artifact))
            assert (refused.value.code, refused.value.detail) == ("precondition_failed", reason)
        assert await rows(db, "SELECT count(*) FROM memory_version WHERE upper_inf(recorded_range)") == [(4,)]
        assert await rows(db, "SELECT id FROM association") == []
        moved = await engine.call(caller("alice"), "move", request("source", "/u/alice/other/"))
        assert isinstance(moved, str)
