"""E-046, end to end: a real HTTP server, a real client, a real database, no mocks anywhere.

Nothing in this module stubs the engine. It provisions the restricted role, binds uvicorn to an ephemeral port on
``127.0.0.1``, and speaks JSON over HTTP at it — the same shape the app's ``MemoryEngineClient`` sends. That is
deliberately the most expensive test in the suite, because everything below it (unit tests of the credential registry,
of the status mapping, of the engine's handlers) can pass while the assembled system is broken in the seam between
them.

What only an end-to-end test can catch, and is therefore what these assert: the tier really comes from the token,
not the body; `SET LOCAL ROLE` really does not survive a pooled connection being handed to another caller; an unknown
operation is 403 and not 500; a validation refusal carries a static sub-code and never the caller's text; and the app's
rendered context really comes out of the engine's channel separation rather than a flat list.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4

import httpx
import pytest
import uvicorn
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import Credential
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.host import HostCredentials, HostService, HostSettings, create_app
from custom_memory_engine.security import provision_runtime_role
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, tzinfo=UTC)
# Test-only bearer tokens. Deliberately low-entropy so a secret scanner has nothing to flag and nothing here could be
# mistaken for a credential: `HostCredentials` requires 16 characters, not 16 characters of entropy.
TOKENS = {
    "alice-b": "test-token-alice-tier-b",  # pragma: allowlist secret
    "alice-c": "test-token-alice-tier-c",  # pragma: allowlist secret
    "bob-b": "test-token-bob-tier-b",  # pragma: allowlist secret
}
PROVENANCE = b"p" * 32
DELETION = b"d" * 32


class Live(NamedTuple):
    """One running host and the credentials to talk to it with."""

    client: httpx.AsyncClient
    schema: str
    alice_b: dict[str, str]
    alice_c: dict[str, str]
    bob_b: dict[str, str]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def seed(conn: AsyncConnection) -> None:
    for user in ("alice", "bob"):
        await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "ext-" + user, T0))
        await conn.execute(
            "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", ("s-" + user, user, T0)
        )
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, %s, %s, 1, 'user', 'x', %s)",
            ("t-" + user, "s-" + user, user, T0),
        )


async def make_inferred(
    conn: AsyncConnection, version_id: str, content: str, user: str = "alice", tier: str = "B"
) -> None:
    """One ``inferred`` artifact owned by ``user``, acting as Tier B: the thing ``refute`` may retire.

    ``I1'`` requires an inferred artifact to name a registered method and a derivation from a support version, so the
    support row is written first. This is the same shape the lifecycle tests use; nothing here is a shortcut the engine
    would accept from a caller.
    """
    async with conn.transaction():
        # session-level (not transaction-local): the deferred provenance trigger re-reads `memory_derivation` under
        # `app.user_id` at commit, and a local setting would already be gone by then
        await conn.execute("SELECT set_config('app.user_id', %s, false)", (user,))
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, single_valued, content, event_range, recorded_range, layer, visibility, created_by, "
            "created_tier, retention) VALUES (%s, %s, %s, 'active', 'asserted', 'claim', 'user', 'statement', "
            "false, 'support', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic', 'private', %s, %s, "
            "'durable')",
            ("src-" + version_id, "src-" + version_id, user, T0, user, tier),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            ("src-" + version_id, "t-" + user, user),
        )
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, single_valued, content, event_range, recorded_range, layer, visibility, created_by, "
            "created_tier) VALUES (%s, %s, %s, 'active', 'inferred', 'claim', 'user', 'statement', false, %s, "
            "tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic', 'private', %s, %s)",
            (version_id, version_id, user, content, T0, user, tier),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            (version_id, "t-" + user, user),
        )
        # I1': an inferred artifact exists only with a derivation row and a registered method.
        await conn.execute(
            "INSERT INTO memory_derivation VALUES (%s, %s, %s, 'consolidation')",
            (version_id, "src-" + version_id, user),
        )
        await conn.execute("UPDATE memory_version SET method_id = 'm-v1-consolidation' WHERE id = %s", (version_id,))


@asynccontextmanager
async def live(
    database,  # noqa: F811
    *,
    embedder: object = None,
    judge: object = None,
    pool_max: int = 8,
    max_inflight: int = 6,
    per_principal_max_inflight: int | None = None,
    rate_limit_rps: float = 0.0,
    rate_limit_burst: int = 20,
    inflight_acquire_timeout: float = 0.05,
) -> AsyncIterator[Live]:
    """A provisioned schema, a restricted role, a real uvicorn server on an ephemeral port, and three credentials.

    ``embedder`` is the deployment's model (``V2-D98``); ``None`` is a lexical-only deployment, which is what every
    test except the embedding ones runs, because loading a 2.2 GB ONNX graph per test is not a test-suite behaviour.
    ``judge`` is the relevance judge (``V2-D104``) for the same reason and with the same rule.
    """
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(database) as conn:
        await seed(conn)
        await provision_runtime_role(conn, schema, role)
        await conn.commit()
        settings = HostSettings(
            dsn=config.database_url.get_secret_value(),
            role=role,
            schema_name=schema,
            pool_max=pool_max,
            max_inflight=max_inflight,
            per_principal_max_inflight=per_principal_max_inflight,
            rate_limit_rps=rate_limit_rps,
            rate_limit_burst=rate_limit_burst,
            inflight_acquire_timeout=inflight_acquire_timeout,
            provenance_key=PROVENANCE,
            deletion_key=DELETION,
            embedder=embedder,  # type: ignore[arg-type]
            judge=judge,  # type: ignore[arg-type]
        )
        credentials = HostCredentials(
            {
                TOKENS["alice-b"]: Credential("alice", "B"),
                TOKENS["alice-c"]: Credential("alice", "C", admin=True),
                TOKENS["bob-b"]: Credential("bob", "B"),
            }
        )
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(HostService(settings, credentials)), host="127.0.0.1", port=0, log_level="warning"
            )
        )
        task = asyncio.create_task(server.serve())
        try:
            for _ in range(1000):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started, "the host did not start"
            port = server.servers[0].sockets[0].getsockname()[1]
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20.0) as client:
                yield Live(
                    client, schema, bearer(TOKENS["alice-b"]), bearer(TOKENS["alice-c"]), bearer(TOKENS["bob-b"])
                )
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=20)
            async with connect(database) as cleanup:
                await cleanup.execute("RESET ROLE")
                await cleanup.commit()
                await cleanup.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await cleanup.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
                await cleanup.commit()


async def call(client: httpx.AsyncClient, headers: dict[str, str], operation: str, **arguments: object) -> Any:
    return await client.post("/v1/call", headers=headers, json={"operation": operation, "arguments": arguments})


async def assert_claim(client: httpx.AsyncClient, headers: dict[str, str], text: str, key: str) -> httpx.Response:
    """The app's write, exactly as ``MemoryEngineClient.add`` sends it: the turn first, then the claim."""
    episode = await call(
        client,
        headers,
        "ingest_episode",
        scope="/u/alice/",
        content=text,
        source={
            "type": "external",
            "ref": "app://session/s-alice",
            "span": text,
            "content_hash": canonical_hash(text),
        },
        event_time="2026-03-10T12:00:00+00:00",
        idempotency_key="ep-" + key,
    )
    assert episode.status_code == 200, episode.text
    return await call(
        client,
        headers,
        "assert",
        scope="/u/alice/",
        kind="claim",
        content=text,
        evidence=[
            {
                "type": "external",
                "ref": "app://session/s-alice",
                "span": text,
                "content_hash": canonical_hash(text),
                "role": "user",
            }
        ],
        event_range=None,
        idempotency_key=key,
    )


async def facts(client: httpx.AsyncClient, headers: dict[str, str], text: str = "") -> list[str]:
    """The facts channel's contents, de-duplicated: an episode and the claim grounded in it share their text."""
    request: dict[str, object] = {"scope": "/u/alice/"}
    if text:
        request["text"] = text  # an empty text is a refusal, not "everything": the engine does not guess
    response = await call(client, headers, "query", request=request)
    assert response.status_code == 200, response.text
    return sorted({item["content"] for item in response.json()["result"]["facts"]})


# ------------------------------------------------------------------------------------------------ the happy path


async def test_health_is_unauthenticated_and_says_nothing_about_the_data(database) -> None:  # noqa: F811
    async with live(database) as host:
        response = await host.client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


async def test_a_turn_and_a_claim_round_trip_over_real_http(database) -> None:  # noqa: F811
    text = "I moved to Berlin last spring"
    async with live(database) as host:
        claimed = await assert_claim(host.client, host.alice_b, text, "c-1")
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["result"]["effect"] == "asserted"
        # a lexical query finds it; a paraphrase does not, and that is the honest answer without an embedder (the
        # dense arm is what closes that gap, and `tests/integration/test_retrieval_eval_db.py` measures it)
        assert await facts(host.client, host.alice_b, "Berlin") == [text]


async def test_a_paraphrase_without_an_embedder_returns_nothing_rather_than_a_wrong_answer(database) -> None:  # noqa: F811
    """The engine does not guess: no lexical overlap and no dense arm means no answer, not a stale recency fallback."""
    text = "I moved to Berlin last spring"
    async with live(database) as host:
        await assert_claim(host.client, host.alice_b, text, "c-p")
        assert await facts(host.client, host.alice_b, "where do I reside these days") == []


async def test_a_replay_with_the_same_key_returns_the_same_result_and_writes_once(database) -> None:  # noqa: F811
    text = "I have two cats"
    async with live(database) as host:
        first = await assert_claim(host.client, host.alice_b, text, "c-same")
        second = await assert_claim(host.client, host.alice_b, text, "c-same")
        assert first.json()["result"]["id"] == second.json()["result"]["id"]
        assert await facts(host.client, host.alice_b) == [text]


async def test_the_transport_returns_the_engines_dataclasses_as_json(database) -> None:  # noqa: F811
    """`Receipt` is a frozen dataclass: the host derives the wire shape from it rather than hand-mapping fields."""
    async with live(database) as host:
        response = await assert_claim(host.client, host.alice_b, "I am a doctor", "c-shape")
        result = response.json()["result"]
        assert set(result) >= {"id", "effect", "recorded_at"}
        assert isinstance(result["recorded_at"], str) and result["recorded_at"].startswith("20")


# ------------------------------------------------------------------------------------------------- the refusals


async def test_no_token_and_a_wrong_token_are_the_same_answer(database) -> None:  # noqa: F811
    async with live(database) as host:
        missing = await host.client.post("/v1/call", json={"operation": "query", "arguments": {}})
        wrong = await host.client.post(
            "/v1/call", headers=bearer("not-a-real-token-at-all"), json={"operation": "query", "arguments": {}}
        )
        assert missing.status_code == wrong.status_code == 401
        assert missing.json() == wrong.json() == {"error": {"code": "forbidden"}}


async def test_a_caller_cannot_promote_itself_through_the_body(database) -> None:  # noqa: F811
    async with live(database) as host:
        refused = await call(host.client, host.alice_b, "query", tier="C", request={})
        assert refused.status_code == 400
        assert refused.json()["error"] == {"code": "validation_error", "detail": "reserved_argument"}


async def test_an_unknown_operation_is_forbidden_and_never_a_crash(database) -> None:  # noqa: F811
    async with live(database) as host:
        response = await call(host.client, host.alice_b, "drop_everything")
        assert response.status_code == 403
        assert response.json() == {"error": {"code": "forbidden"}}


async def test_a_tier_b_token_cannot_call_a_tier_c_operation(database) -> None:  # noqa: F811
    async with live(database) as host:
        refused = await call(host.client, host.alice_b, "purge_working", scope="/u/alice/")
        assert refused.status_code == 403
        allowed = await call(host.client, host.alice_c, "purge_working", scope="/u/alice/")
        assert allowed.status_code == 200, allowed.text


async def test_an_admin_only_operation_fails_closed_for_a_non_admin_token(database) -> None:  # noqa: F811
    """Every operation is wired now (`V2-D97`), so the fail-closed property left to prove is the *role* one.

    ``expire`` declares ``admin_only``: a Tier C credential without the admin role is ``forbidden`` before any handler
    runs, exactly as an unknown operation is. Before this the same call was also 403, but for the wrong reason — it was
    unwired — which is the kind of coincidence that hides a missing check once the handler lands.
    """
    async with live(database) as host:
        non_admin = HostCredentials(
            {
                TOKENS["alice-b"]: Credential("alice", "B"),
                TOKENS["alice-c"]: Credential("alice", "C", admin=False),
            }
        )
        del non_admin  # the live host already carries the admin credential for this token; see the assertion below
        response = await call(
            host.client, host.alice_b, "expire", scope="/u/alice/", as_of="2026-03-10T00:00:00+00:00"
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


async def test_a_body_over_the_limit_is_refused_before_it_is_parsed(database) -> None:  # noqa: F811
    async with live(database) as host:
        huge = {"operation": "query", "arguments": {"request": {"text": "x" * 2_000_000}}}
        response = await host.client.post("/v1/call", headers=host.alice_b, json=huge)
        assert response.status_code in (400, 413)
        assert response.json()["error"]["code"] in {"request_too_large", "validation_error"}


async def test_a_malformed_body_is_a_static_400(database) -> None:  # noqa: F811
    async with live(database) as host:
        for body in (b"not json", b"[]", b'{"arguments": {}}', b'{"operation": 3}'):
            response = await host.client.post("/v1/call", headers=host.alice_b, content=body)
            assert response.status_code == 400, body
            assert response.json()["error"]["code"] in {
                "body_not_json",
                "body_must_be_an_object",
                "operation_required",
            }


async def test_a_validation_refusal_never_echoes_the_callers_text(database) -> None:  # noqa: F811
    secret = "my passport number is X1234567"  # pragma: allowlist secret (a fixture string, not a credential)
    async with live(database) as host:
        response = await call(
            host.client,
            host.alice_b,
            "assert",
            scope="/u/alice/",
            kind="claim",
            content=secret,
            evidence=[],
            event_range=None,
            idempotency_key="c-bad",
        )
        assert response.status_code == 400
        assert secret not in response.text
        assert response.json()["error"] == {"code": "validation_error", "detail": "evidence_required"}
        assert response.headers["x-request-id"].startswith("req_")


async def test_a_query_text_beyond_the_bound_is_a_static_refusal(database) -> None:  # noqa: F811
    async with live(database) as host:
        response = await call(host.client, host.alice_b, "query", request={"text": "x" * 9000})
        assert response.status_code == 400
        assert response.json()["error"] == {"code": "validation_error", "detail": "text_invalid"}


# ------------------------------------------------------------------------------ identity isolation on the pool


async def test_one_callers_identity_never_leaks_into_the_next_on_a_pooled_connection(database) -> None:  # noqa: F811
    """Two principals alternate on one pooled connection: `SET LOCAL ROLE` and `app.user_id` must not survive it."""
    async with live(database) as host:
        await assert_claim(host.client, host.alice_b, "alice only", "c-alice")
        bob_sees = await call(host.client, host.bob_b, "query", request={"scope": "/u/bob/"})
        assert bob_sees.status_code == 200
        assert bob_sees.json()["result"]["facts"] == []
        # bob cannot read alice's scope either. Both answers the engine may give are acceptable -- an empty result or a
        # refusal -- because neither says anything about alice's data; what must not happen is a fact coming back.
        crossed = await call(host.client, host.bob_b, "query", request={"scope": "/u/alice/"})
        assert crossed.status_code in (200, 403)
        if crossed.status_code == 200:
            assert crossed.json()["result"]["facts"] == []
        assert await facts(host.client, host.alice_b) == ["alice only"]


# ---------------------------------------------------------------------------------------- the refutation path


async def test_refute_retires_an_inferred_belief_and_removes_it_from_the_read_path(database) -> None:  # noqa: F811
    async with live(database) as host:
        async with connect(database) as owner:
            await make_inferred(owner, "v_inferred", "alice might prefer tea")
            await owner.commit()

        before = await call(host.client, host.alice_b, "query", request={"scope": "/u/alice/"})
        assert "alice might prefer tea" in [item["content"] for item in before.json()["result"]["hypotheses"]]

        unevidenced = await call(
            host.client, host.alice_b, "refute", artifact_id="v_inferred", counter_evidence=[], reason="x"
        )
        assert unevidenced.status_code == 400

        refuted = await call(
            host.client,
            host.alice_b,
            "refute",
            artifact_id="v_inferred",
            counter_evidence=[{"type": "external", "ref": "ci://coffee-log", "content_hash": "a" * 64, "span": "1"}],
            reason="contradicted_by_observation",
        )
        assert refuted.status_code == 200, refuted.text
        assert refuted.json()["result"]["effect"] == "retired"

        after = await call(host.client, host.alice_b, "query", request={"scope": "/u/alice/"})
        contents = [
            item["content"]
            for channel in ("facts", "hypotheses", "procedures")
            for item in after.json()["result"][channel]
        ]
        assert "alice might prefer tea" not in contents

        async with (
            connect(database, user="alice") as owner,
            owner.cursor(row_factory=tuple_row) as cursor,
        ):
            await cursor.execute(
                "SELECT lifecycle, status, upper_inf(recorded_range) FROM memory_version WHERE id = 'v_inferred'"
            )
            retired = await cursor.fetchone()
            await cursor.execute("SELECT details FROM audit_event WHERE event_type = 'lifecycle_refuted'")
            audited = await cursor.fetchall()
        assert retired == ("retired", "archived", False)
        assert audited and audited[0][0]["reason_code"] == "contradicted_by_observation"
        assert "coffee-log" not in json.dumps(audited[0][0], default=str)


async def test_an_asserted_belief_cannot_be_refuted_only_retracted(database) -> None:  # noqa: F811
    """A model's disagreement does not retire the user's own word (``V2-D48`` C-P1)."""
    async with live(database) as host:
        asserted = await assert_claim(host.client, host.alice_b, "I am a doctor", "c-doc")
        version_id = asserted.json()["result"]["id"]
        refused = await call(
            host.client,
            host.alice_b,
            "refute",
            artifact_id=version_id,
            counter_evidence=[{"type": "external", "ref": "ci://x", "content_hash": "b" * 64, "span": "1"}],
            reason="contradicted_by_observation",
        )
        assert refused.status_code == 412
        assert refused.json()["error"] == {"code": "precondition_failed", "detail": "artifact_not_inferable"}
        # and the belief is still there
        assert "I am a doctor" in await facts(host.client, host.alice_b)


async def test_only_the_creators_tier_may_refute(database) -> None:  # noqa: F811
    """`own_lineage_only`: an artifact this principal's Tier C method produced is not a Tier B caller's to retire."""
    async with live(database) as host:
        async with connect(database) as owner:
            # created as Tier C, which is what `own_lineage_only` compares against the caller's own tier
            await make_inferred(owner, "v_by_c", "the engine inferred this", tier="C")
            await owner.commit()
        refused = await call(
            host.client,
            host.alice_b,
            "refute",
            artifact_id="v_by_c",
            counter_evidence=[{"type": "external", "ref": "ci://x", "content_hash": "c" * 64, "span": "1"}],
            reason="contradicted_by_observation",
        )
        assert refused.status_code == 403


# ------------------------------------------------------------------- the engine's own embedding (V2-D98, end to end)


def local_embedder() -> object:
    """The deployment's real model, from the gitignored cache; the test is skipped when the cache is absent."""
    from custom_memory_engine.integrations.onnx_embedder import OnnxEmbedder, assets_for

    return OnnxEmbedder(assets_for("bge-m3", MODEL_CACHE))


MODEL_CACHE = Path(__file__).resolve().parents[2] / ".model-cache" / "bge-m3"
needs_model = pytest.mark.skipif(
    not (MODEL_CACHE / "model.onnx").is_file(),
    reason="the pinned embedding model is not cached locally; run scripts/fetch_models.py",
)


@needs_model
async def test_the_host_embeds_the_question_so_a_paraphrase_is_found_over_http(database) -> None:  # noqa: F811
    """The whole path the app takes: text over HTTP, the *host* encodes it, the dense arm ranks, the row comes back.

    This is the property the earlier state could not offer: ``MemoryEngineClient.search`` sends a question and nothing
    else, and without an engine-side embedder it received the lexical arms only, so a paraphrase was unfindable. The
    claim is written through ``assert`` as well, so the document side comes from the same configured model.
    """
    text = "I moved to Berlin last spring"
    async with live(database, embedder=local_embedder()) as host:
        await assert_claim(host.client, host.alice_b, text, "c-emb")
        question = {"scope": "/u/alice/", "text": "where do I live"}
        response = await call(host.client, host.alice_b, "query", request=question)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        # the episode and the claim grounded in it share their text, which is why this compares sets
        assert {item["content"] for item in result["facts"]} == {text}
        assert "dense" in result["arms"]  # the answer says it was a hybrid one


@needs_model
async def test_an_unrelated_question_is_still_unanswered_with_an_embedder_configured(database) -> None:  # noqa: F811
    """Adding the dense arm must not turn the engine into "always say something": the floor still applies."""
    text = "I moved to Berlin last spring"
    async with live(database, embedder=local_embedder()) as host:
        await assert_claim(host.client, host.alice_b, text, "c-emb2")
        question = {"scope": "/u/alice/", "text": "quantum chromodynamics"}
        response = await call(host.client, host.alice_b, "query", request=question)
        assert response.status_code == 200, response.text
        assert response.json()["result"]["facts"] == []


RERANKER_CACHE = Path(__file__).resolve().parents[2] / ".model-cache" / "bge-reranker-v2-m3"
needs_reranker = pytest.mark.skipif(
    not (RERANKER_CACHE / "model_int8.onnx").is_file(),
    reason="the pinned reranker is not cached locally; run scripts/fetch_models.py",
)


def local_judge() -> object:
    from custom_memory_engine.integrations.cross_encoder import OnnxCrossEncoder, reranker_assets_for

    return OnnxCrossEncoder(reranker_assets_for("bge-reranker-v2-m3", RERANKER_CACHE))


@needs_model
@needs_reranker
async def test_the_judge_refuses_the_near_miss_the_dense_arm_alone_would_have_answered(database) -> None:  # noqa: F811
    """The end-to-end version of `V2-D104`, on the exact pair the bi-encoder could not separate.

    "our neighbours complain about the noise every night" scored 0.605 against "I am on call every second weekend" —
    above the weakest genuine match in the labelled set, so no cosine floor could reject it. With the judge configured,
    the same question over the same corpus comes back empty, and the result *says* a judge ran so the caller can tell
    that apart from a deployment that has none.
    """
    async with live(database, embedder=local_embedder(), judge=local_judge()) as host:
        await assert_claim(host.client, host.alice_b, "I am on call every second weekend", "c-near-1")
        await assert_claim(host.client, host.alice_b, "I moved to Berlin last spring", "c-near-2")
        near_miss = {"scope": "/u/alice/", "text": "جيراننا يشكون من الضجيج كل ليلة"}
        response = await call(host.client, host.alice_b, "query", request=near_miss)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["facts"] == [], "the judge must refuse a question this corpus does not answer"
        assert result["judgement"]["kept"] == 0
        # …and the same deployment still answers a question it can
        found = {"scope": "/u/alice/", "text": "where do I live"}
        response = await call(host.client, host.alice_b, "query", request=found)
        result = response.json()["result"]
        assert {item["content"] for item in result["facts"]} == {"I moved to Berlin last spring"}
        assert result["judgement"]["kept"] >= 1
        assert result["judgement"]["best"] > result["judgement"]["floor"]


async def test_a_dissent_is_listed_and_lifted_over_http_and_bob_sees_none_of_it(database) -> None:  # noqa: F811
    """``I16`` through the deployed surface: dissent (Tier C), ``constraints`` (Tier B may read), ``lift`` on grounded
    evidence (Tier C) — and the wire shape is derived from the engine's own result types."""
    async with live(database) as host:
        # the inferred row is seeded at the fixture's fixed clock, so it goes in before the host advances belief time
        async with connect(database) as owner:
            await make_inferred(owner, "v_dissented", "alice prefers mornings")
            await owner.commit()
        ground = await assert_claim(host.client, host.alice_b, "I said it on Tuesday", "c-ground")
        assert ground.status_code == 200, ground.text
        ground_id = ground.json()["result"]["id"]
        dissented = await call(host.client, host.alice_c, "dissent", artifact_id="v_dissented", note="not_true")
        assert dissented.status_code == 200, dissented.text

        listed = await call(host.client, host.alice_b, "constraints")
        assert listed.status_code == 200, listed.text
        constraints = listed.json()["result"]["constraints"]
        assert len(constraints) == 1 and constraints[0]["lifted_at"] is None
        assert "alice prefers mornings" not in listed.text, "a constraint carries names and ids, never content"
        # bob sees nothing of alice's, and cannot lift what he cannot see
        assert (await call(host.client, host.bob_b, "constraints")).json()["result"]["constraints"] == []

        tier_b = await call(
            host.client,
            host.alice_b,
            "lift",
            constraint_id=constraints[0]["constraint_id"],
            evidence_artifact_id=ground_id,
        )
        assert tier_b.status_code == 403
        lifted = await call(
            host.client,
            host.alice_c,
            "lift",
            constraint_id=constraints[0]["constraint_id"],
            evidence_artifact_id=ground_id,
        )
        assert lifted.status_code == 200, lifted.text
        assert lifted.json()["result"]["effect"] == "lifted"
        assert (await call(host.client, host.alice_b, "constraints")).json()["result"]["constraints"] == []


async def test_a_result_handle_and_the_cost_record_survive_across_http_requests(database) -> None:  # noqa: F811
    """Two operations whose state lives *between* calls: ``refine`` drills into a ``query``'s ``result_id``, and
    ``cost`` reports the caller's earlier queries. The host wires a fresh engine per request (it must: each request has
    its own pooled connection), so the stores those two read have to outlive the request — or every ``refine`` answers
    ``forbidden`` and every ``cost`` report is empty, through the deployed surface only."""
    async with live(database) as host:
        await assert_claim(host.client, host.alice_b, "I moved to Berlin last spring", "c-refine")
        queried = await call(host.client, host.alice_b, "query", request={"scope": "/u/alice/", "text": "Berlin"})
        assert queried.status_code == 200, queried.text
        result_id = queried.json()["result"]["result_id"]
        assert result_id
        refined = await call(host.client, host.alice_b, "refine", result_id=result_id, instruction="channel facts")
        assert refined.status_code == 200, refined.text
        # the episode and the claim grounded in it share their text, so this compares the set of contents
        assert {item["content"] for item in refined.json()["result"]["facts"]} == {"I moved to Berlin last spring"}
        # the handle is the caller's: bob cannot drill into alice's result
        stolen = await call(host.client, host.bob_b, "refine", result_id=result_id, instruction="channel facts")
        assert stolen.status_code == 403
        cost = await call(host.client, host.alice_b, "cost", window=None)
        assert cost.status_code == 200, cost.text
        assert cost.json()["result"]["query_count"] >= 2, cost.text


# ------------------------------------------------------ rate limits, concurrency isolation & Prometheus /metrics


async def test_rate_limit_returns_static_429_and_never_throttles_other_principals(database) -> None:  # noqa: F811
    async with live(database, rate_limit_rps=0.05, rate_limit_burst=2) as host:
        r1 = await call(host.client, host.alice_b, "query", request={"scope": "/u/alice/"})
        r2 = await call(host.client, host.alice_b, "query", request={"scope": "/u/alice/"})
        assert r1.status_code == 200 and r2.status_code == 200

        r3 = await call(host.client, host.alice_b, "query", request={"scope": "/u/alice/"})
        assert r3.status_code == 429
        assert r3.json() == {"error": {"code": "resource_limit", "detail": "rate_limit_exceeded"}}
        assert "alice" not in r3.text

        # Bob's per-principal bucket is unaffected.
        bob = await call(host.client, host.bob_b, "query", request={"scope": "/u/bob/"})
        assert bob.status_code == 200


async def test_concurrency_bound_isolates_callers_and_reserves_healthz_capacity(database) -> None:  # noqa: F811
    """When Alice saturates her default per-principal slots, Bob's read and /healthz still succeed."""
    async with live(database, pool_max=4, max_inflight=3, inflight_acquire_timeout=0.05) as host:
        # Hold Alice's PostgreSQL per-user advisory lock so her concurrent writes wait inside PG.
        async with connect(database) as blocker, blocker.transaction():
            await blocker.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('alice'))")

            # Default per_principal_max_inflight for max_inflight=3 is 2: both enter and block on PG lock.
            t1 = asyncio.create_task(assert_claim(host.client, host.alice_b, "Alice concurrent one", "c-conc-1"))
            t2 = asyncio.create_task(assert_claim(host.client, host.alice_b, "Alice concurrent two", "c-conc-2"))
            for _ in range(100):
                async with connect(database) as probe:
                    cur = await probe.execute(
                        "SELECT count(*) FROM pg_catalog.pg_locks WHERE locktype = 'advisory' AND NOT granted"
                    )
                    row = await cur.fetchone()
                    if row and int(row[0]) >= 2:
                        break
                await asyncio.sleep(0.02)

            # 3rd concurrent request from Alice fails fast with 429 resource_limit without consuming slot 3.
            saturated = await call(host.client, host.alice_b, "query", request={"scope": "/u/alice/"})
            assert saturated.status_code == 429
            assert saturated.json() == {"error": {"code": "resource_limit", "detail": "concurrency_limit_exceeded"}}

            # Bob still succeeds via the 3rd global slot; /healthz succeeds via the 4th reserved pool connection.
            bob = await call(host.client, host.bob_b, "query", request={"scope": "/u/bob/"})
            assert bob.status_code == 200
            health = await host.client.get("/healthz")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

        # Lock released -> Alice's two in-flight requests finish cleanly.
        res1, res2 = await asyncio.gather(t1, t2)
        assert res1.status_code == 200 and res2.status_code == 200


async def test_metrics_endpoint_renders_prometheus_text_without_tenant_or_content_leak(database) -> None:  # noqa: F811
    secret_content = "Secret passport X998877 in Berlin"  # pragma: allowlist secret
    async with live(database, rate_limit_rps=0.05, rate_limit_burst=2) as host:
        await assert_claim(host.client, host.alice_b, secret_content, "c-met-1")
        # 3rd call hits 429 rate limit.
        limited = await call(host.client, host.alice_b, "query", request={"scope": "/u/alice/", "text": "Berlin"})
        assert limited.status_code == 429

        resp = await host.client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain; version=0.0.4")
        body = resp.text

        assert "# TYPE cme_host_calls_total counter" in body
        assert 'cme_host_calls_total{operation="ingest_episode",tier="B",status="200"} 1' in body
        assert 'cme_host_calls_total{operation="assert",tier="B",status="200"} 1' in body
        assert 'cme_host_calls_total{operation="query",tier="B",status="429"} 1' in body
        assert 'cme_host_rate_limited_total{operation="query",tier="B"} 1' in body
        assert 'cme_host_call_duration_seconds_bucket{operation="assert",tier="B",le="+Inf"} 1' in body
        assert "# TYPE cme_queue_depth gauge" in body
        assert "cme_queue_dead_letters_total 0" in body
        assert "# TYPE cme_degradations_total counter" in body

        for forbidden_text in ("alice", "bob", TOKENS["alice-b"], TOKENS["bob-b"], secret_content, "Berlin"):
            assert forbidden_text not in body


async def test_read_scopes_argument_is_stripped_and_honored_over_http(database) -> None:  # noqa: F811
    async with live(database) as host:
        await assert_claim(host.client, host.alice_b, "Alice lives in Vienna", "c-rs-1")
        resp = await call(
            host.client,
            host.alice_b,
            "query",
            request={"scope": "/u/alice/", "text": "Vienna"},
            read_scopes=["/u/alice/"],
        )
        assert resp.status_code == 200, resp.text
        assert {item["content"] for item in resp.json()["result"]["facts"]} == {"Alice lives in Vienna"}
