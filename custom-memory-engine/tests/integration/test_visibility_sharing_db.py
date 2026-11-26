"""`I7` end to end (`V2-D113`, #244): an owner shares a row, a grantee reads exactly it, and nothing else changes.

Driven over the real host (uvicorn, real tokens, the restricted role, forced RLS), because every property here is the
composition of three layers that are each correct alone and were never joined before: the writer (`set_visibility`,
or `visibility` at insertion), the grant (`grant_access`/`revoke_access`), and the read paths, which used to filter
every statement by `user_id = <caller>` and therefore could never return a granted row even when RLS admitted it.

Each test states the property in its name; the controls are inside the tests (the same read before sharing, after
revocation, after lowering visibility), so a check that cannot fail would show up as a control that passes.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from psycopg.rows import tuple_row

from custom_memory_engine.grounding.sources import canonical_hash
from tests.integration.test_host_e2e_db import assert_claim, call, connect, live
from tests.integration.test_migration_m1 import database  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

SHARED = "Alice shares her notes about the Lisbon trip"
PRIVATE = "Alice keeps a private diary about the Lisbon trip"


async def claim_id(client: httpx.AsyncClient, headers: dict[str, str], text: str, key: str) -> str:
    response = await assert_claim(client, headers, text, key)
    assert response.status_code == 200, response.text
    return str(response.json()["result"]["id"])


async def bobs_facts(client: httpx.AsyncClient, headers: dict[str, str]) -> set[str]:
    """What bob's query over alice's namespace returns: the read set asks for it, the grant decides."""
    response = await call(
        client, headers, "query", request={"scope": "/u/alice/", "text": "Lisbon trip"}, read_scopes=["/u/alice/"]
    )
    assert response.status_code == 200, response.text
    return {item["content"] for item in response.json()["result"]["facts"]}


async def scope_reads(database: Any, user: str) -> int:  # noqa: F811
    async with connect(database) as conn, conn.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            "SELECT count(*) FROM audit_event WHERE user_id = %s AND event_type = 'scope_read'", (user,)
        )
        row = await cursor.fetchone()
        assert row is not None
        return int(row[0])


async def test_a_grantee_reads_exactly_the_shared_row_and_revocation_closes_it(database) -> None:  # noqa: F811
    async with live(database) as host:
        shared = await claim_id(host.client, host.alice_b, SHARED, "share-1")
        private = await claim_id(host.client, host.alice_b, PRIVATE, "share-2")
        grant = await call(
            host.client, host.alice_c, "grant_access", scope="/u/alice/", principal="bob", idempotency_key="g-1"
        )
        assert grant.status_code == 200, grant.text
        # control: a grant alone exposes nothing, because every row is private until its owner says otherwise
        assert await bobs_facts(host.client, host.bob_b) == set()

        raised = await call(host.client, host.alice_c, "set_visibility", artifact_id=shared, visibility="shared")
        assert raised.status_code == 200, raised.text
        assert raised.json()["result"]["effect"] == "visibility_changed"

        before = await scope_reads(database, "bob")
        assert await bobs_facts(host.client, host.bob_b) == {SHARED}
        assert await scope_reads(database, "bob") == before + 1  # rule 6: the cross-scope read left its row
        # a read of another namespace names it (the read set is requested, then intersected with the grants)
        got = await call(host.client, host.bob_b, "get", artifact_id=shared, read_scopes=["/u/alice/"])
        assert got.status_code == 200, got.text
        assert got.json()["result"]["content"] == SHARED
        # the shared row's evidence is readable with it (child readability is the parent's), so it renders as a named
        # source rather than an opaque handle. A 200 alone would not say that: the route answers 200 either way.
        lineage = await call(
            host.client, host.bob_b, "provenance", artifact_id=shared, depth=1, read_scopes=["/u/alice/"]
        )
        assert lineage.status_code == 200, lineage.text
        view = lineage.json()["result"]
        references = [edge["reference"] for edge in view["edges"]]
        assert references, "the shared row's evidence is returned, not withheld"
        # `readable` is what separates a named source from an opaque handle; the route answers 200 either way
        assert all(reference["readable"] is True for reference in references), references
        assert all(reference.get("source_id") for reference in references), references
        assert all(reference.get("source_type") for reference in references), references
        # the private row stays private through the same grant: absent and unreadable are one answer
        hidden = await call(host.client, host.bob_b, "get", artifact_id=private, read_scopes=["/u/alice/"])
        assert hidden.status_code == 403
        assert hidden.json() == {"error": {"code": "forbidden"}}

        revoked = await call(host.client, host.alice_c, "revoke_access", grant_id=grant.json()["result"])
        assert revoked.status_code == 200, revoked.text
        # with no grant left, naming alice's scope is a denial of the whole read, not an empty page of it
        after = await call(
            host.client,
            host.bob_b,
            "query",
            request={"scope": "/u/alice/", "text": "Lisbon trip"},
            read_scopes=["/u/alice/"],
        )
        assert after.status_code == 403
        assert after.json() == {"error": {"code": "forbidden"}}
        closed = await call(host.client, host.bob_b, "get", artifact_id=shared, read_scopes=["/u/alice/"])
        assert closed.status_code == 403


async def test_lowering_visibility_back_to_private_closes_the_read_under_a_live_grant(database) -> None:  # noqa: F811
    async with live(database) as host:
        shared = await claim_id(host.client, host.alice_b, SHARED, "low-1")
        await call(host.client, host.alice_c, "grant_access", scope="/u/alice/", principal="bob", idempotency_key="g")
        await call(host.client, host.alice_c, "set_visibility", artifact_id=shared, visibility="project")
        assert await bobs_facts(host.client, host.bob_b) == {SHARED}
        lowered = await call(host.client, host.alice_c, "set_visibility", artifact_id=shared, visibility="private")
        assert lowered.status_code == 200, lowered.text
        assert await bobs_facts(host.client, host.bob_b) == set()


async def test_visibility_set_at_insertion_is_honoured_and_stays_in_the_owners_namespace(database) -> None:  # noqa: F811
    async with live(database) as host:
        await call(host.client, host.alice_c, "grant_access", scope="/u/alice/", principal="bob", idempotency_key="g")
        private = await assert_claim(host.client, host.alice_b, SHARED, "ins-0")  # an ordinary private row
        assert private.status_code == 200
        text = "Alice posted the Lisbon trip itinerary for everyone"
        response = await call(
            host.client,
            host.alice_b,
            "ingest_episode",
            scope="/u/alice/",
            content=text,
            source={
                "type": "external",
                "ref": "app://itinerary",
                "span": "Lisbon trip itinerary",
                "content_hash": canonical_hash(text),
            },
            event_time="2026-03-10T12:00:00+00:00",
            idempotency_key="ins-1",
            visibility="shared",
        )
        assert response.status_code == 200, response.text
        assert text in await bobs_facts(host.client, host.bob_b)
        assert SHARED not in await bobs_facts(host.client, host.bob_b)
        # a visibility outside the vocabulary is a grammar fault, never a silent private write
        refused = await call(
            host.client,
            host.alice_b,
            "assert",
            scope="/u/alice/",
            kind="claim",
            content=SHARED,
            evidence=[
                {
                    "type": "external",
                    "ref": "app://x",
                    "span": SHARED,
                    "content_hash": canonical_hash(SHARED),
                    "role": "user",
                }
            ],
            event_range=None,
            idempotency_key="ins-3",
            visibility="public",
        )
        assert refused.status_code == 400
        assert refused.json()["error"] == {"code": "validation_error", "detail": "visibility_invalid"}


async def test_a_grantee_can_read_a_shared_row_but_never_change_it(database) -> None:  # noqa: F811
    """`V2-D53`: holding a grant authorises reads only. Every write a grantee might try on the row is refused."""
    async with live(database) as host:
        shared = await claim_id(host.client, host.alice_b, SHARED, "w-1")
        await call(host.client, host.alice_c, "grant_access", scope="/u/alice/", principal="bob", idempotency_key="g")
        await call(host.client, host.alice_c, "set_visibility", artifact_id=shared, visibility="shared")
        assert await bobs_facts(host.client, host.bob_b) == {SHARED}

        attempts = {
            "set_visibility": await call(
                host.client, host.bob_c, "set_visibility", artifact_id=shared, visibility="private"
            ),
            "retract_own": await call(host.client, host.bob_b, "retract_own", artifact_id=shared, reason="not_mine"),
            "dissent": await call(host.client, host.bob_c, "dissent", artifact_id=shared, note="disagree"),
            "delete": await call(
                host.client, host.bob_c, "delete", scope="/u/alice/", target_ref=shared, deletion_epoch=0
            ),
            "assert_into_alice": await call(
                host.client,
                host.bob_b,
                "assert",
                scope="/u/alice/",
                kind="claim",
                content="Bob writes into alice's namespace",
                evidence=[{"type": "external", "ref": "x", "span": "x", "content_hash": "0" * 64, "role": "user"}],
                event_range=None,
                idempotency_key="bob-1",
            ),
        }
        for operation, response in attempts.items():
            assert response.status_code in (400, 403, 412), (operation, response.status_code, response.text)
        # and the row is exactly as alice left it: still shared, still active, still readable by bob
        assert await bobs_facts(host.client, host.bob_b) == {SHARED}
        async with connect(database) as conn, conn.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute("SELECT visibility, lifecycle FROM memory_version WHERE id = %s", (shared,))
            assert await cursor.fetchone() == ("shared", "asserted")


async def test_set_visibility_refuses_what_is_not_the_callers_and_records_a_content_free_audit(database) -> None:  # noqa: F811
    async with live(database) as host:
        alices = await claim_id(host.client, host.alice_b, SHARED, "a-1")
        bobs_text = "Bob's own note about the Lisbon trip"
        bobs_claim = await call(
            host.client,
            host.bob_b,
            "assert",
            scope="/u/bob/",
            kind="claim",
            content=bobs_text,
            evidence=[
                {
                    "type": "external",
                    "ref": "app://bob",
                    "span": bobs_text,
                    "content_hash": canonical_hash(bobs_text),
                    "role": "user",
                }
            ],
            event_range=None,
            idempotency_key="b-1",
        )
        assert bobs_claim.status_code == 200, bobs_claim.text
        bobs = bobs_claim.json()["result"]["id"]

        foreign = await call(host.client, host.alice_c, "set_visibility", artifact_id=bobs, visibility="shared")
        unknown = await call(host.client, host.alice_c, "set_visibility", artifact_id="v_nope", visibility="shared")
        # another tenant's id and an id that does not exist are the same answer (T-11)
        assert foreign.status_code == unknown.status_code == 403
        assert foreign.json() == unknown.json() == {"error": {"code": "forbidden"}}
        invalid = await call(host.client, host.alice_c, "set_visibility", artifact_id=alices, visibility="world")
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "validation_error"
        tier_b = await call(host.client, host.alice_b, "set_visibility", artifact_id=alices, visibility="shared")
        assert tier_b.status_code == 403
        async with connect(database) as conn, conn.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute("SELECT visibility FROM memory_version WHERE id = %s", (bobs,))
            assert await cursor.fetchone() == ("private",)

        changed = await call(host.client, host.alice_c, "set_visibility", artifact_id=alices, visibility="shared")
        assert changed.status_code == 200, changed.text
        async with connect(database) as conn, conn.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                "SELECT details::text FROM audit_event WHERE user_id = 'alice' AND event_type = 'visibility_changed'"
            )
            details = [row[0] for row in await cursor.fetchall()]
        assert len(details) == 1
        assert '"visibility": "shared"' in details[0] and '"artifact_kind": "memory_version"' in details[0]
        assert alices not in details[0] and "Lisbon" not in details[0]


async def test_scope_identity_is_immutable_even_for_the_owner(database) -> None:  # noqa: F811
    """`ARCHITECTURE_V2.md` 5 rule 5, enforced by migration 32: no in-place re-homing, even inside one's namespace."""
    async with live(database) as host:
        alices = await claim_id(host.client, host.alice_b, SHARED, "s-1")
        async with connect(database) as conn:
            await conn.execute("SELECT set_config('app.user_id', 'alice', false)")
            for statement in (
                "UPDATE memory_version SET scope_path = '/u/alice/p/elsewhere/' WHERE id = %s",
                "UPDATE memory_version SET visibility = visibility WHERE id = %s",
            ):
                try:
                    async with conn.transaction():
                        await conn.execute(statement, (alices,))
                    outcome = "ok"
                except Exception as error:  # noqa: BLE001 - the error text is what is asserted
                    outcome = str(error).splitlines()[0]
                if "scope_path" in statement:
                    assert outcome == "scope_identity_is_immutable"
                else:
                    assert outcome == "ok"  # control: the guard is about scope, not about every update
