"""The memory-engine boundary: the app talks to ChronaMind, not to ``mem0ai`` (``V2-D93``).

The app and the engine run on different interpreters on purpose, so the boundary is a wire contract rather than an
import. These tests hold both halves of that contract:

* the client sends the engine's own public operations, in the delegating envelope ``{operation, principal, arguments}``,
  with the arguments the engine accepts;
* the rendering keeps the four channels' *epistemic labels*, and renders a recorded turn (an ``episode``) as earlier
  conversation rather than as a known fact, because a flat list of "memories" is the defect the engine exists to remove;
* a user message is single-valued only when the slot classifier names its slot (``V2-D115``), and only the newest
  user message is classified (``V2-D121``);
* nothing is cached: a second search reflects the engine's current answer (``V2-D116``);
* nothing on the agent's path imports ``mem0ai`` any more, and a deployment that configures no engine gets no
  long-term memory rather than a silent fallback;
* the content hash this module computes is the engine's canonical form, so a replay on the other side matches.

These run without a server. The same contract against a real engine host and database is
``tests/integration/test_app_engine_boundary.py``.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

import httpx
import pytest

from app.services.memory_engine import MemoryEngineClient, _render, canonical_hash
from tests.slot_fakes import current, recorded_classifier

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests on asyncio: the app's own event loop, not trio's."""
    return "asyncio"


def transport(handler) -> httpx.MockTransport:  # type: ignore[no-untyped-def]
    """A mock transport that records every request and answers with ``handler(request, body)``."""
    return httpx.MockTransport(handler)


class Recorder:
    """Records ``(path, body)`` pairs and answers from a scripted table, wrapped as the host wraps it (``result``)."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        """Record every request; answer ``{"result": responses[operation]}``."""
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self._responses = responses or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request, recording it first."""
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path, body))
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        operation = body.get("operation") if isinstance(body, dict) else None
        return httpx.Response(200, json={"result": self._responses.get(str(operation), {})})

    def operations(self) -> list[str]:
        """The operation names the client sent, in order."""
        return [body.get("operation", path) for path, body in self.requests]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEMORY_ENGINE_URL", "http://engine.test", raising=False)
    monkeypatch.setattr(settings, "MEMORY_ENGINE_TOKEN", "token", raising=False)


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEMORY_ENGINE_URL", "", raising=False)


# ------------------------------------------------------------------------------------------- the agent's three calls


async def test_search_sends_the_engines_own_query_operation(configured) -> None:  # type: ignore[no-untyped-def]
    recorder = Recorder({"query": {"facts": [{"kind": "claim", "content": "Alice lives in Berlin"}]}})
    client = MemoryEngineClient(transport=transport(recorder))
    rendered = await client.search("alice", "where do I live")

    assert rendered == "Known facts:\n* Alice lives in Berlin"
    path, body = recorder.requests[-1]
    assert path == "/v1/call"
    assert body["operation"] == "query"
    # the delegating envelope: the end user is named, and the tier is never sent (it comes from the token)
    assert body["principal"] == "alice"
    assert "tier" not in body
    assert body["arguments"]["request"]["text"] == "where do I live"
    assert body["arguments"]["request"]["scope"] == "/u/alice/"


async def test_a_hypothesis_is_labelled_and_a_conflict_is_shown_as_unresolved(configured) -> None:  # type: ignore[no-untyped-def]
    """The property a flat "memories" list cannot express, and why the rendering matters."""
    recorder = Recorder(
        {
            "query": {
                "facts": [{"kind": "claim", "content": "Alice lives in Berlin"}],
                "hypotheses": [{"content": "Alice prefers morning meetings"}],
                "conflicts": [{"content": "The CRM says Munich"}],
            }
        }
    )
    client = MemoryEngineClient(transport=transport(recorder))
    rendered = await client.search("alice", "what do I know about Alice")

    assert "Known facts:\n* Alice lives in Berlin" in rendered
    assert "Hypotheses (inferred, not confirmed):\n* Alice prefers morning meetings" in rendered
    assert "Open contradictions (unresolved):\n* The CRM says Munich" in rendered


async def test_an_anonymous_session_gets_no_long_term_memory_and_no_request(configured) -> None:  # type: ignore[no-untyped-def]
    """A shared partition would leak one caller's facts into another's prompt."""
    recorder = Recorder()
    client = MemoryEngineClient(transport=transport(recorder))
    assert await client.search(None, "anything") == ""
    assert recorder.requests == []


async def test_add_records_the_turn_before_it_asserts_a_claim(configured) -> None:  # type: ignore[no-untyped-def]
    """Ordering is the fast lane: the turn is durable even if the claim write fails."""
    recorder = Recorder()
    classifier, _ = recorded_classifier({"I moved to Berlin last spring": current("residence", "Berlin")})
    client = MemoryEngineClient(transport=transport(recorder), slot_classifier=classifier)
    await client.add(
        "alice",
        [
            {"role": "user", "content": "I moved to Berlin last spring"},
            {"role": "assistant", "content": "Noted"},
        ],
    )

    # both turns are recorded (each is durable and deletable); only the user's becomes a claim, and it does so right
    # after its own episode, so that episode is the evidence the claim cites
    assert recorder.operations() == ["ingest_episode", "assert", "ingest_episode"]
    episode = recorder.requests[0][1]["arguments"]
    assert episode["source"]["type"] == "external"
    assert episode["source"]["content_hash"] == canonical_hash("I moved to Berlin last spring")
    assert {body["principal"] for _, body in recorder.requests} == {"alice"}
    assert all("tier" not in body for _, body in recorder.requests)
    claim = recorder.requests[1][1]["arguments"]
    # a clear change of residence is single-valued with its slot, so the engine supersedes the previous one (V2-D115)
    assert (claim["cardinality"], claim["semantic_slot"]) == ("single", "residence")
    assert claim["evidence"][0]["content_hash"] == canonical_hash("I moved to Berlin last spring")
    # the app keeps the transcript, so it is the one that says who spoke: without this the engine quarantines the
    # claim as third-party evidence (V2-D96), which would make long-term memory write-only
    assert claim["evidence"][0]["role"] == "user"
    assert episode["source"]["role"] == "user"


async def test_a_message_that_is_not_a_clear_state_update_is_multi_and_names_no_slot(configured) -> None:  # type: ignore[no-untyped-def]
    """A single-valued claim without a slot would be exclusive with every other one, so ``multi`` sends no slot."""
    recorder = Recorder()
    client = MemoryEngineClient(transport=transport(recorder))
    await client.add("alice", [{"role": "user", "content": "I visited Paris last year and loved it"}])

    claim = recorder.requests[1][1]["arguments"]
    assert claim["cardinality"] == "multi"
    assert "semantic_slot" not in claim


async def test_only_the_newest_user_message_is_classified_and_earlier_ones_replay_as_multi(configured) -> None:  # type: ignore[no-untyped-def]
    """A resend of the whole history costs one model call, for the one message this call records first (V2-D121).

    The earlier user message was the newest one of an earlier call; its ``assert`` replays by idempotency key, so its
    arguments are not read again and it is not sent to the model again.
    """
    recorder = Recorder()
    classifier, model = recorded_classifier(
        {"أنا ساكن في برلين": current("residence", "برلين"), "انتقلت إلى باريس": current("residence", "باريس")}
    )
    client = MemoryEngineClient(transport=transport(recorder), slot_classifier=classifier)
    await client.add(
        "alice",
        [
            {"role": "user", "content": "أنا ساكن في برلين"},
            {"role": "assistant", "content": "تمام"},
            {"role": "user", "content": "انتقلت إلى باريس"},
            {"role": "assistant", "content": "مبروك"},
        ],
        session_id="s-1",
    )

    assert model.asked == ["انتقلت إلى باريس"]
    claims = [body["arguments"] for _, body in recorder.requests if body.get("operation") == "assert"]
    assert [(claim["content"], claim["cardinality"], claim.get("semantic_slot")) for claim in claims] == [
        ("أنا ساكن في برلين", "multi", None),
        ("انتقلت إلى باريس", "single", "residence"),
    ]


async def test_an_assistant_message_is_recorded_but_never_becomes_a_claim(configured) -> None:  # type: ignore[no-untyped-def]
    """An assistant's text is not evidence about the user."""
    recorder = Recorder()
    client = MemoryEngineClient(transport=transport(recorder))
    await client.add("alice", [{"role": "assistant", "content": "You live in Berlin"}])
    assert recorder.operations() == ["ingest_episode"]


# ------------------------------------------------------------------------------------------------------ no cache


async def test_a_second_search_reflects_the_engines_current_answer_and_never_a_cached_render(configured) -> None:  # type: ignore[no-untyped-def]
    """``V2-D116``: a rendered context is plaintext memory, so a client-side copy would outlive a deletion.

    The engine's answer changes between two identical searches (as it does after a deletion or a supersession); the
    second search must ask the engine again and render the new answer.
    """
    answers = [
        {"facts": [{"kind": "claim", "content": "I live in Berlin"}]},
        {"facts": []},
    ]
    calls: list[str] = []

    def engine(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["operation"])
        return httpx.Response(200, json={"result": answers[len(calls) - 1]})

    client = MemoryEngineClient(transport=transport(engine))
    assert await client.search("alice", "where do I live") == "Known facts:\n* I live in Berlin"
    assert await client.search("alice", "where do I live") == ""
    assert calls == ["query", "query"]


def test_the_client_module_has_no_cache_dependency() -> None:
    """The regression guard for ``V2-D116``: the content cache cannot come back through an import."""
    import app.services.memory_engine as module

    assert not hasattr(module, "cache_service")
    assert not hasattr(module, "cache_key")


# ------------------------------------------------------------------------------------------------ failure behaviour


async def test_an_engine_refusal_degrades_to_no_memory_and_never_raises(configured) -> None:  # type: ignore[no-untyped-def]
    """The host's error envelope (``{"error": {"code", "detail"}}`` with a 4xx) is a failure, not an empty answer."""

    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "validation_error", "detail": "principal_required"}})

    client = MemoryEngineClient(transport=transport(refuse))
    assert await client.search("alice", "where do I live") == ""
    assert await client.add("alice", [{"role": "user", "content": "I live in Berlin"}]) is None


async def test_a_response_without_the_result_envelope_renders_nothing(configured) -> None:  # type: ignore[no-untyped-def]
    """The host wraps every answer in ``result``; top-level channels are not the contract and are not read."""

    def unwrapped(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"facts": [{"kind": "claim", "content": "I live in Berlin"}]})

    client = MemoryEngineClient(transport=transport(unwrapped))
    assert await client.search("alice", "where do I live") == ""


async def test_an_unreachable_engine_degrades_to_no_memory_and_never_raises(configured) -> None:  # type: ignore[no-untyped-def]

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = MemoryEngineClient(transport=transport(fail))
    assert await client.search("alice", "where do I live") == ""
    assert await client.add("alice", [{"role": "user", "content": "x"}]) is None


async def test_an_unconfigured_deployment_has_no_memory_and_makes_no_request(unconfigured) -> None:  # type: ignore[no-untyped-def]
    """No silent fallback to mem0: 'not configured' means no long-term memory, not a weaker one."""
    recorder = Recorder()
    client = MemoryEngineClient(transport=transport(recorder))
    assert client.backend == "none"
    assert await client.search("alice", "q") == ""
    await client.add("alice", [{"role": "user", "content": "x"}])
    assert recorder.requests == []


async def test_a_pre_warm_failure_is_logged_and_swallowed(configured) -> None:  # type: ignore[no-untyped-def]
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = MemoryEngineClient(transport=transport(fail))
    assert await client.initialize() is None


# ---------------------------------------------------------------------------------------------- the shared contract


def test_the_content_hash_matches_the_engines_canonical_form() -> None:
    r"""``grounding.sources.canonical_hash``: NFC, ``\\r\\n``/``\\r`` -> ``\\n``, UTF-8, SHA-256.

    Duplicated rather than imported, because the two projects share no code. This is the test that keeps the
    duplication from drifting: both sides agree on these vectors.
    """
    for text in ("plain", "line\r\nbreak", "carriage\rreturn", "e\u0301 composed"):
        expected = hashlib.sha256(
            unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        ).hexdigest()
        assert canonical_hash(text) == expected
    assert canonical_hash("a\r\nb") == canonical_hash("a\nb")


def test_rendering_omits_an_empty_channel_and_never_invents_one() -> None:
    assert _render({}) == ""
    assert _render({"facts": [], "hypotheses": []}) == ""
    assert _render({"facts": [{"content": "  "}]}) == ""


def test_no_module_on_the_agents_path_imports_mem0() -> None:
    """The regression this phase exists to prevent: the agent running on the retired store."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from mem0" in text or "import mem0" in text:
            offenders.append(path.name)
    # `app/services/memory.py` is the legacy mem0 implementation; nothing on the agent's path imports it.
    assert offenders == ["memory.py"], offenders
    for path in (root / "app/core/langgraph/graph.py", root / "app/main.py"):
        assert "services.memory " not in path.read_text(encoding="utf-8"), path


def test_an_episode_is_earlier_conversation_never_a_known_fact() -> None:
    """The engine returns recorded turns in ``facts`` (``kind == "episode"``); only claims are "Known facts".

    An episode is verbatim, unattributed and never superseded: after "I live in Berlin" then "I moved to Paris" the
    Berlin turn is still a recorded turn, and an assistant's turn ("You live in Madrid") is one too.
    """
    rendered = _render(
        {
            "facts": [
                {"kind": "episode", "content": "I live in Berlin"},
                {"kind": "episode", "content": "You live in Madrid now"},
                {"kind": "claim", "content": "I moved to Paris"},
            ]
        }
    )
    assert rendered == (
        "Known facts:\n* I moved to Paris\n"
        "Earlier conversation (verbatim; may be outdated; not confirmed facts):\n"
        "* I live in Berlin\n* You live in Madrid now"
    )


def test_an_episode_whose_text_is_already_rendered_is_dropped() -> None:
    """A claim is grounded in the turn that said it, so both carry one text: the claim is rendered, the turn is not."""
    rendered = _render(
        {
            "facts": [
                {"kind": "episode", "content": "I moved to Paris"},
                {"kind": "claim", "content": "I moved to Paris"},
                {"kind": "episode", "content": " I moved to Paris "},
            ],
            "conflicts": [{"content": "The CRM says Munich"}],
        }
    )
    assert rendered == "Known facts:\n* I moved to Paris\nOpen contradictions (unresolved):\n* The CRM says Munich"


def test_only_episodes_render_no_known_facts_heading() -> None:
    rendered = _render({"facts": [{"kind": "episode", "content": "hello"}, {"kind": "episode", "content": "hello"}]})
    assert rendered == "Earlier conversation (verbatim; may be outdated; not confirmed facts):\n* hello"
    assert "Known facts" not in rendered
