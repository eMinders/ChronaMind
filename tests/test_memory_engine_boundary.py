"""The memory-engine boundary: the app talks to ChronaMind, not to ``mem0ai`` (``V2-D93``).

The app and the engine run on different interpreters on purpose, so the boundary is a wire contract rather than an
import. These tests hold both halves of that contract:

* the client sends the engine's own public operations, with the arguments the engine accepts;
* the rendering keeps the four channels' *epistemic labels*, because a flat list of "memories" is the defect the engine
  exists to remove;
* nothing on the agent's path imports ``mem0ai`` any more, and a deployment that configures no engine gets no
  long-term memory rather than a silent fallback;
* the content hash this module computes is the engine's canonical form, so a replay on the other side matches.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

import httpx
import pytest

from app.services.memory_engine import MemoryEngineClient, _render, canonical_hash

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests on asyncio: the app's own event loop, not trio's."""
    return "asyncio"


def transport(handler) -> httpx.MockTransport:  # type: ignore[no-untyped-def]
    """A mock transport that records every request and answers with ``handler(request, body)``."""
    return httpx.MockTransport(handler)


class Recorder:
    """Records ``(path, body)`` pairs and answers from a scripted table."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        """Record every request; answer from ``responses`` keyed by operation."""
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self._responses = responses or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request, recording it first."""
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path, body))
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        operation = body.get("operation") if isinstance(body, dict) else None
        return httpx.Response(200, json=self._responses.get(str(operation), {}))

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


async def cache_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pass-through cache, so the test measures the client and not Redis."""

    class _Cache:
        async def get(self, _key: str) -> None:
            return None

        async def set(self, _key: str, _value: str) -> None:
            return None

    import app.services.memory_engine as module

    monkeypatch.setattr(module, "cache_service", _Cache())


# ------------------------------------------------------------------------------------------- the agent's three calls


async def test_search_sends_the_engines_own_query_operation(configured, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await cache_stub(monkeypatch)
    recorder = Recorder({"query": {"facts": [{"content": "Alice lives in Berlin"}]}})
    client = MemoryEngineClient(transport=transport(recorder))
    rendered = await client.search("alice", "where do I live")

    assert rendered == "Known facts:\n* Alice lives in Berlin"
    path, body = recorder.requests[-1]
    assert path == "/v1/call"
    assert body["operation"] == "query"
    assert body["tier"] == "B"
    assert body["arguments"]["request"]["text"] == "where do I live"
    assert body["arguments"]["request"]["scope"] == "/u/alice/"


async def test_a_hypothesis_is_labelled_and_a_conflict_is_shown_as_unresolved(configured, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The property a flat "memories" list cannot express, and why the rendering matters."""
    await cache_stub(monkeypatch)
    recorder = Recorder(
        {
            "query": {
                "facts": [{"content": "Alice lives in Berlin"}],
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


async def test_add_records_the_turn_before_it_asserts_a_claim(configured, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Ordering is the fast lane: the turn is durable even if the claim write fails."""
    await cache_stub(monkeypatch)
    recorder = Recorder()
    client = MemoryEngineClient(transport=transport(recorder))
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
    claim = recorder.requests[1][1]["arguments"]
    assert claim["cardinality"] == "multi"  # the safe default: nothing is silently treated as a single-valued fact
    assert claim["evidence"][0]["content_hash"] == canonical_hash("I moved to Berlin last spring")
    # the app keeps the transcript, so it is the one that says who spoke: without this the engine quarantines the
    # claim as third-party evidence (V2-D96), which would make long-term memory write-only
    assert claim["evidence"][0]["role"] == "user"
    assert episode["source"]["role"] == "user"


async def test_an_assistant_message_is_recorded_but_never_becomes_a_claim(configured, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An assistant's text is not evidence about the user."""
    await cache_stub(monkeypatch)
    recorder = Recorder()
    client = MemoryEngineClient(transport=transport(recorder))
    await client.add("alice", [{"role": "assistant", "content": "You live in Berlin"}])
    assert recorder.operations() == ["ingest_episode"]


# ------------------------------------------------------------------------------------------------ failure behaviour


async def test_an_unreachable_engine_degrades_to_no_memory_and_never_raises(configured, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await cache_stub(monkeypatch)

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
    # `app/services/memory.py` is the legacy implementation, retained only until `E-046` lands the engine-side host.
    assert offenders == ["memory.py"], offenders
    for path in (root / "app/core/langgraph/graph.py", root / "app/main.py"):
        assert "services.memory " not in path.read_text(encoding="utf-8"), path
