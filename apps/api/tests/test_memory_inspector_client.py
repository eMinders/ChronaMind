"""The memory inspector's client: a Tier C delegating token, used by the inspector routes and never by the agent.

`MemoryEngineClient.call` is the one engine call that does not swallow failures: the inspector must show the user what
the engine answered. These tests pin which token each client sends, and how every refusal surfaces.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.services.memory_engine import EngineCallError, MemoryEngineClient, memory_engine, memory_inspector
from app.services.memory_slots import NullSlotClassifier

AGENT_TOKEN = "agent-token-tier-b"  # pragma: allowlist secret
INSPECTOR_TOKEN = "inspector-token-tier-c"  # pragma: allowlist secret


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests on asyncio: the app's own event loop."""
    return "asyncio"


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MEMORY_ENGINE_URL", "http://engine.test", raising=False)
    monkeypatch.setattr(settings, "MEMORY_ENGINE_TOKEN", AGENT_TOKEN, raising=False)
    monkeypatch.setattr(settings, "MEMORY_ENGINE_USER_TOKEN", INSPECTOR_TOKEN, raising=False)


def inspector(handler: Any) -> MemoryEngineClient:
    """An inspector client, configured like the module's, over a mock transport."""
    return MemoryEngineClient(
        transport=httpx.MockTransport(handler),
        slot_classifier=NullSlotClassifier(),
        token_setting="MEMORY_ENGINE_USER_TOKEN",
        read_timeout=15.0,
    )


def answer(status: int, body: object) -> Any:
    """A handler that records the authorization header and answers ``body`` with ``status``."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


@pytest.mark.anyio
async def test_the_inspector_sends_the_tier_c_token_and_the_end_user_as_principal() -> None:
    handler = answer(200, {"result": {"facts": []}})
    client = inspector(handler)
    result = await client.call("42", "query", {"request": {"mode": "current_state"}})

    assert result == {"facts": []}
    request = handler.seen[0]
    assert request.headers["Authorization"] == f"Bearer {INSPECTOR_TOKEN}"
    envelope = json.loads(request.content)
    assert envelope == {"operation": "query", "principal": "42", "arguments": {"request": {"mode": "current_state"}}}
    # the tier is never sent: the host derives it from the token (T-34)
    assert "tier" not in envelope and "tier" not in envelope["arguments"]


@pytest.mark.anyio
async def test_the_agents_client_never_sends_the_inspector_token() -> None:
    """Regression guard: the Tier C token can delete and export for every user; the agent must stay on Tier B."""
    handler = answer(200, {"result": {}})
    client = MemoryEngineClient(transport=httpx.MockTransport(handler), slot_classifier=NullSlotClassifier())
    await client.search("42", "anything")

    assert handler.seen[0].headers["Authorization"] == f"Bearer {AGENT_TOKEN}"
    assert INSPECTOR_TOKEN not in str(handler.seen[0].headers)


def test_the_module_clients_read_different_token_settings_and_timeouts() -> None:
    assert memory_engine._token_setting == "MEMORY_ENGINE_TOKEN"
    assert memory_inspector._token_setting == "MEMORY_ENGINE_USER_TOKEN"
    assert memory_engine._read_timeout == 5.0
    assert memory_inspector._read_timeout == 15.0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "validation_error"),
        (403, "forbidden"),
        (409, "retryable_conflict"),
        (412, "precondition_failed"),
        (429, "resource_limit"),
        (500, "internal_error"),
    ],
)
async def test_every_refusal_raises_with_the_engines_code_and_status(status: int, code: str) -> None:
    client = inspector(answer(status, {"error": {"code": code, "detail": "some_sub_code"}}))
    with pytest.raises(EngineCallError) as raised:
        await client.call("42", "get", {"artifact_id": "v-1"})

    assert raised.value.code == code
    assert raised.value.status == status
    assert raised.value.detail == "some_sub_code"


@pytest.mark.anyio
async def test_an_unreachable_host_is_engine_unavailable_with_no_status() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(EngineCallError) as raised:
        await inspector(refuse).call("42", "query", {})
    assert (raised.value.code, raised.value.status) == ("engine_unavailable", 0)


@pytest.mark.anyio
@pytest.mark.parametrize("body", [{"result": "not-an-object"}, {"unexpected": 1}, ["a", "list"]])
async def test_a_200_without_a_result_object_is_an_internal_error(body: object) -> None:
    with pytest.raises(EngineCallError) as raised:
        await inspector(answer(200, body)).call("42", "query", {})
    assert raised.value.code == "internal_error"


@pytest.mark.anyio
async def test_health_is_true_only_for_a_200_from_healthz() -> None:
    assert await inspector(answer(200, {"status": "ok"})).health() is True
    assert await inspector(answer(503, {"status": "starting"})).health() is False

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await inspector(refuse).health() is False
