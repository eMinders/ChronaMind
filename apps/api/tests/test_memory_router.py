"""The memory inspector routes (`apps/api/app/api/v1/memory.py`) against a scripted engine host: no server, no database.

What these tests hold, route by route: the principal is always the signed-in user, the request each route sends is
the engine's own operation and arguments, every engine refusal reaches the caller with its static code, a recorded turn
is never shown as a fact, and "forget me" erases before it clears and reports what it observed. The same routes are
proven against a real engine host in `tests/integration/test_memory_router_live.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.v1.memory as memory_routes
from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.main import app
from app.models.user import User
from app.services.memory_engine import MemoryEngineClient
from app.services.memory_slots import NullSlotClassifier

USER_ID = 42


def artifact(artifact_id: str, kind: str, content: str, *, lifecycle: str = "asserted",
             channel: str = "facts") -> dict[str, Any]:  # fmt: skip
    """An engine `Artifact` as the host serialises it, including the scope path the router must not forward."""
    return {
        "artifact_id": artifact_id,
        "bound": None,
        "channel": channel,
        "conflict_sides": [],
        "content": content,
        "hop_distance": 0,
        "kind": kind,
        "label": None,
        "lifecycle": lifecycle,
        "preconditions": None,
        "provenance": [
            {
                "source_id": f"app://session/turn-{artifact_id}",
                "scope_path": f"/u/{USER_ID}/",
                "source_type": "external",
                "derivation_type": "evidence",
                "readable": True,
            }
        ],
        "scope_path": f"/u/{USER_ID}/",
        "steps": None,
        "support_summary": "",
        "verification": "unverified",
    }


PARIS = artifact("v-paris", "claim", "I moved to Paris")
BERLIN_TURN = artifact("v-turn", "episode", "I live in Berlin", lifecycle="observed")
MEMORY = {"facts": [PARIS, BERLIN_TURN], "hypotheses": [], "procedures": [], "conflicts": []}
RECEIPT = {
    "request_id": "engine-request-digest",
    "scope": "all",
    "deletion_epoch": 2,
    "deleted_versions": 3,
    "dependents": 0,
    "rederived_dependents": 0,
    "purged_jobs": 0,
    "external_exclusions": [],
    "external_exclusion_count": 0,
    "retired_dependents": 0,
    "deleted_turns": 2,
    "deleted_entities": 0,
    "deleted_mentions": 0,
}


@dataclass
class FakeEngine:
    """A scripted engine host. ``answers[operation]`` is a list consumed in order (the last one repeats).

    An answer is either a result object (HTTP 200) or ``(status, code)`` for a refusal.
    """

    answers: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    healthy: bool = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request from the script, recording it."""
        if request.url.path == "/healthz":
            return httpx.Response(200 if self.healthy else 503, json={})
        envelope = json.loads(request.content)
        self.calls.append(envelope)
        queue = self.answers.get(envelope["operation"], [{}])
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, tuple):
            status, code = answer
            return httpx.Response(status, json={"error": {"code": code}})
        return httpx.Response(200, json={"result": answer})

    def operations(self) -> list[str]:
        """The operation names sent, in order."""
        return [call["operation"] for call in self.calls]


@dataclass
class FakeChatStore:
    """Stands in for the agent's checkpointer and the session table: records what Forget cleared."""

    sessions: dict[int, list[str]] = field(default_factory=lambda: {USER_ID: ["s-1", "s-2"], 7: ["s-other"]})
    cleared: list[str] = field(default_factory=list)
    advanced: list[str] = field(default_factory=list)
    log: list[str] | None = None
    failing: set[str] = field(default_factory=set)
    unlisted: bool = False

    async def get_user_sessions(self, user_id: int) -> list[Any]:
        """The sessions of ``user_id`` only."""
        if self.unlisted:
            raise RuntimeError("session table unavailable")
        return [type("ChatSession", (), {"id": session_id})() for session_id in self.sessions.get(user_id, [])]

    async def clear_chat_history(self, session_id: str) -> None:
        """Record a cleared transcript, or fail for a session in ``failing``."""
        if session_id in self.failing:
            raise RuntimeError("checkpointer unavailable")
        self.cleared.append(session_id)
        if self.log is not None:
            self.log.append(f"clear:{session_id}")

    async def advance_history_epoch(self, session_id: str) -> int:
        """Record an advanced history epoch."""
        self.advanced.append(session_id)
        return 1


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    fake = FakeEngine(answers={"query": [MEMORY]})
    monkeypatch.setattr(settings, "MEMORY_ENGINE_URL", "http://engine.test", raising=False)
    monkeypatch.setattr(settings, "MEMORY_ENGINE_USER_TOKEN", "inspector-token", raising=False)
    client = MemoryEngineClient(
        transport=httpx.MockTransport(fake),
        slot_classifier=NullSlotClassifier(),
        token_setting="MEMORY_ENGINE_USER_TOKEN",
        read_timeout=15.0,
    )
    monkeypatch.setattr(memory_routes, "memory_inspector", client)
    return fake


@pytest.fixture
def chats(monkeypatch: pytest.MonkeyPatch) -> FakeChatStore:
    store = FakeChatStore()
    monkeypatch.setattr(memory_routes, "database_service", store)
    monkeypatch.setattr(memory_routes, "agent", store)
    return store


@pytest.fixture
def client(engine: FakeEngine) -> Iterator[TestClient]:
    limiter.reset()
    app.dependency_overrides[get_current_user] = lambda: User(id=USER_ID, email="u@example.com", hashed_password="x")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def code_of(response: httpx.Response) -> str:
    return response.json()["detail"]["code"]


# -- who the routes act for --------------------------------------------------------------------------------------


def test_the_list_acts_as_the_signed_in_user_and_sends_current_state_without_text(client, engine) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/v1/memory/", params={"principal": "7", "tier": "C"})

    assert response.status_code == 200
    (call,) = engine.calls
    assert call["principal"] == str(USER_ID)
    assert "tier" not in call and "tier" not in call["arguments"]
    assert call["arguments"] == {"request": {"mode": "current_state", "scope": "/u/42/", "limit": 200}}


def test_a_principal_in_a_body_is_ignored(client, engine) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/v1/memory/search", json={"text": "Paris", "principal": "7"})
    assert engine.calls[0]["principal"] == str(USER_ID)


def test_every_route_but_health_needs_the_user_token(engine) -> None:  # type: ignore[no-untyped-def]
    limiter.reset()
    anonymous = TestClient(app)
    for method, path in (("get", "/api/v1/memory/"), ("get", "/api/v1/memory/v-1"), ("post", "/api/v1/memory/forget")):
        assert getattr(anonymous, method)(path).status_code in (401, 403)
    assert anonymous.get("/api/v1/memory/health").status_code == 200
    assert engine.calls == []


# -- what the routes return ---------------------------------------------------------------------------------------


def test_a_recorded_turn_is_listed_as_conversation_never_as_a_known_fact(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/memory/").json()

    assert [item["content"] for item in body["known_facts"]] == ["I moved to Paris"]
    assert [item["content"] for item in body["conversation"]] == ["I live in Berlin"]


def test_no_response_carries_a_scope_path(client, engine) -> None:  # type: ignore[no-untyped-def]
    engine.answers["get"] = [PARIS]
    engine.answers["provenance"] = [
        {
            "artifact_id": "v-paris",
            "depth": 1,
            "depth_limited": False,
            "edges": [{"depth": 1, "reference": PARIS["provenance"][0]}],
        }  # fmt: skip
    ]
    for path in ("/api/v1/memory/", "/api/v1/memory/v-paris", "/api/v1/memory/v-paris/provenance"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "/u/42/" not in response.text and "scope_path" not in response.text


def test_search_text_is_bounded_before_any_engine_call(client, engine) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/api/v1/memory/search", json={"text": "P"}).status_code == 422
    assert client.post("/api/v1/memory/search", json={"text": "x" * 301}).status_code == 422
    assert engine.calls == []

    assert client.post("/api/v1/memory/search", json={"text": "Paris"}).status_code == 200
    assert engine.calls[0]["arguments"]["request"]["text"] == "Paris"


# -- errors -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("engine_status", "engine_code", "http_status"),
    [
        (400, "validation_error", 422),
        (403, "forbidden", 404),
        (412, "precondition_failed", 409),
        (429, "resource_limit", 429),
        (409, "retryable_conflict", 409),
        (500, "internal_error", 502),
    ],
)
def test_every_engine_refusal_reaches_the_caller_with_its_code(  # type: ignore[no-untyped-def]
    client, engine, engine_status: int, engine_code: str, http_status: int
) -> None:
    engine.answers["get"] = [(engine_status, engine_code)]
    response = client.get("/api/v1/memory/v-1")

    assert response.status_code == http_status
    assert code_of(response) == engine_code


def test_an_unreachable_engine_is_502_engine_unavailable(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(
        memory_routes,
        "memory_inspector",
        MemoryEngineClient(
            transport=httpx.MockTransport(refuse),
            slot_classifier=NullSlotClassifier(),
            token_setting="MEMORY_ENGINE_USER_TOKEN",
        ),  # fmt: skip
    )
    response = client.get("/api/v1/memory/")
    assert (response.status_code, code_of(response)) == (502, "engine_unavailable")


def test_an_unknown_engine_shape_is_502_not_an_empty_screen(client, engine) -> None:  # type: ignore[no-untyped-def]
    engine.answers["query"] = [{"facts": [{"artifact_id": "v-1", "kind": "memory", "lifecycle": "asserted"}]}]
    response = client.get("/api/v1/memory/")
    assert (response.status_code, code_of(response)) == (502, "engine_response_invalid")


def test_an_unconfigured_inspector_is_503_and_health_says_so(client, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "MEMORY_ENGINE_USER_TOKEN", "", raising=False)

    response = client.get("/api/v1/memory/")
    assert (response.status_code, code_of(response)) == (503, "inspector_not_configured")
    assert client.get("/api/v1/memory/health").json() == {"engine": "not_configured"}
    assert engine.calls == []


def test_health_reports_up_and_down(client, engine) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/memory/health").json() == {"engine": "up"}
    engine.healthy = False
    assert client.get("/api/v1/memory/health").json() == {"engine": "down"}


# -- time ---------------------------------------------------------------------------------------------------------


def test_the_timeline_asks_audit_as_of_with_one_instant_and_no_text(client, engine) -> None:  # type: ignore[no-untyped-def]
    engine.answers["query"] = [{"audit": [artifact("v-berlin", "claim", "I live in Berlin", channel="none")]}]
    at = "2026-11-20T10:00:00+00:00"
    response = client.get("/api/v1/memory/timeline", params={"at": at})

    assert response.status_code == 200
    assert [item["content"] for item in response.json()["beliefs"]] == ["I live in Berlin"]
    request = engine.calls[0]["arguments"]["request"]
    assert request["mode"] == "audit_as_of"
    assert request["target_time"] == request["belief_time"] == at
    assert "text" not in request and "reference_time" not in request
    # `/timeline` is a route of its own, never read as an artifact id
    assert engine.operations() == ["query"]


@pytest.mark.parametrize(
    "at",
    ["2026-11-20T10:00:00", (datetime.now(UTC) + timedelta(days=1)).isoformat()],
    ids=["naive", "future"],
)
def test_a_naive_or_future_instant_is_refused_before_the_engine(client, engine, at: str) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/v1/memory/timeline", params={"at": at})
    assert (response.status_code, code_of(response)) == (422, "validation_error")
    assert engine.calls == []


def test_changes_need_a_nonempty_window_and_resolve_ids(client, engine) -> None:  # type: ignore[no-untyped-def]
    start, end = "2026-11-20T10:00:00+00:00", "2026-11-20T11:00:00+00:00"
    reversed_window = client.get("/api/v1/memory/changes", params={"start": end, "end": start})
    assert (reversed_window.status_code, code_of(reversed_window)) == (422, "validation_error")
    assert engine.calls == []

    engine.answers["query"] = [
        {"facts": [PARIS], "changes": [{"outcome": "changed", "previous": ["v-gone"], "current": ["v-paris"]}]}
    ]
    response = client.get("/api/v1/memory/changes", params={"start": start, "end": end})
    assert response.status_code == 200
    (change,) = response.json()["changes"]
    assert change["outcome"] == "changed" and change["previous"] == []
    assert [item["id"] for item in change["current"]] == ["v-paris"]
    request = engine.calls[0]["arguments"]["request"]
    assert (request["mode"], request["window_start"], request["window_end"]) == ("change", start, end)


# -- adjudicate ---------------------------------------------------------------------------------------------------


def test_confirm_sends_adjudicate_and_returns_the_receipt(client, engine) -> None:  # type: ignore[no-untyped-def]
    engine.answers["adjudicate"] = [{"id": "v-held", "effect": "asserted", "recorded_at": "2026-11-27T10:00:00+00:00"}]
    response = client.post("/api/v1/memory/v-held/adjudicate", json={"decision": "confirm", "note": "yes"})

    assert response.status_code == 200
    assert response.json()["effect"] == "asserted"
    assert engine.calls[0]["arguments"] == {"artifact_id": "v-held", "decision": "confirm", "note": "yes"}


def test_adjudicating_an_item_no_longer_contested_is_409(client, engine) -> None:  # type: ignore[no-untyped-def]
    engine.answers["adjudicate"] = [(412, "precondition_failed")]
    response = client.post("/api/v1/memory/v-held/adjudicate", json={"decision": "deny"})
    assert (response.status_code, code_of(response)) == (409, "precondition_failed")


def test_a_decision_other_than_confirm_or_deny_is_refused(client, engine) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/api/v1/memory/v-held/adjudicate", json={"decision": "promote"}).status_code == 422
    assert engine.calls == []


# -- forget -------------------------------------------------------------------------------------------------------


def test_forget_needs_the_typed_word(client, engine, chats) -> None:  # type: ignore[no-untyped-def]
    for body in ({}, {"confirm": "delete"}, {"confirm": "yes"}):
        assert client.post("/api/v1/memory/forget", json=body).status_code == 422
    assert engine.calls == [] and chats.cleared == []


def test_forget_erases_first_then_clears_only_this_users_sessions_then_checks(client, engine, chats) -> None:  # type: ignore[no-untyped-def]
    engine.answers = {
        "query": [MEMORY, {}],  # before: one fact and one turn; after: nothing
        "delete": [RECEIPT],
        "export": [{"artifact_count": 0}],
        "get": [(403, "forbidden")],
    }
    response = client.post("/api/v1/memory/forget", json={"confirm": "DELETE"})

    assert response.status_code == 200
    assert engine.operations() == ["query", "delete", "query", "export", "get"]
    assert engine.calls[1]["arguments"] == {"scope": "all"}
    assert chats.cleared == ["s-1", "s-2"] and chats.advanced == ["s-1", "s-2"]  # never "s-other"
    body = response.json()
    assert (body["cleared_chat_sessions"], body["failed_chat_sessions"]) == (2, 0)
    assert body["checks"] == {
        "chat_history_cleared": True,
        "memory_list_empty": True,
        "export_artifact_count": 0,
        "old_item_not_found": True,
    }
    assert body["receipt"]["deleted_versions"] == 3
    assert "engine-request-digest" not in response.text


def test_a_failed_erasure_clears_no_chat_session(client, engine, chats) -> None:  # type: ignore[no-untyped-def]
    engine.answers = {"query": [MEMORY], "delete": [(400, "validation_error")]}
    response = client.post("/api/v1/memory/forget", json={"confirm": "DELETE"})

    assert (response.status_code, code_of(response)) == (422, "validation_error")
    assert chats.cleared == [] and chats.advanced == []


def test_a_retryable_conflict_is_retried_exactly_once(client, engine, chats) -> None:  # type: ignore[no-untyped-def]
    engine.answers = {"query": [MEMORY, {}], "delete": [(409, "retryable_conflict"), RECEIPT],
                      "export": [{"artifact_count": 0}], "get": [(403, "forbidden")]}  # fmt: skip
    assert client.post("/api/v1/memory/forget", json={"confirm": "DELETE"}).status_code == 200
    assert engine.operations().count("delete") == 2

    engine.calls.clear()
    engine.answers = {"query": [MEMORY], "delete": [(409, "retryable_conflict")]}
    response = client.post("/api/v1/memory/forget", json={"confirm": "DELETE"})
    assert (response.status_code, code_of(response)) == (409, "retryable_conflict")
    assert engine.operations().count("delete") == 2


def test_a_check_that_cannot_run_is_reported_as_unverified_not_as_passed(client, engine, chats) -> None:  # type: ignore[no-untyped-def]
    engine.answers = {"query": [MEMORY, (500, "internal_error")], "delete": [RECEIPT],
                      "export": [(500, "internal_error")], "get": [(500, "internal_error")]}  # fmt: skip
    response = client.post("/api/v1/memory/forget", json={"confirm": "DELETE"})

    assert response.status_code == 200  # the erasure committed: the receipt is still the answer
    assert response.json()["checks"] == {
        "chat_history_cleared": True,
        "memory_list_empty": None,
        "export_artifact_count": None,
        "old_item_not_found": None,
    }


def test_an_item_still_readable_after_forget_is_reported_as_a_failed_check(client, engine, chats) -> None:  # type: ignore[no-untyped-def]
    engine.answers = {"query": [MEMORY, MEMORY], "delete": [RECEIPT], "export": [{"artifact_count": 2}],
                      "get": [PARIS]}  # fmt: skip
    checks = client.post("/api/v1/memory/forget", json={"confirm": "DELETE"}).json()["checks"]
    assert checks == {
        "chat_history_cleared": True,
        "memory_list_empty": False,
        "export_artifact_count": 2,
        "old_item_not_found": False,
    }


FORGET_OK = {
    "query": [MEMORY, {}],
    "delete": [RECEIPT],
    "export": [{"artifact_count": 0}],
    "get": [(403, "forbidden")],
}


def test_a_transcript_that_cannot_be_cleared_makes_the_forget_incomplete(client, engine, chats) -> None:  # type: ignore[no-untyped-def]
    """Review finding on #307: a swallowed clear let Forget report success while a transcript survived."""
    engine.answers = {key: list(value) for key, value in FORGET_OK.items()}
    chats.failing = {"s-2"}
    body = client.post("/api/v1/memory/forget", json={"confirm": "DELETE"}).json()

    assert (body["cleared_chat_sessions"], body["failed_chat_sessions"]) == (1, 1)
    assert body["checks"]["chat_history_cleared"] is False
    assert chats.cleared == ["s-1"] and chats.advanced == ["s-1"]

    # Forget is idempotent: once the checkpointer recovers, calling it again finishes the clearing.
    engine.calls.clear()
    engine.answers = {key: list(value) for key, value in FORGET_OK.items()}
    chats.failing = set()
    retry = client.post("/api/v1/memory/forget", json={"confirm": "DELETE"}).json()
    assert retry["failed_chat_sessions"] == 0 and retry["checks"]["chat_history_cleared"] is True
    assert "s-2" in chats.cleared


def test_an_unreadable_session_list_is_a_failed_clear_not_a_success(client, engine, chats) -> None:  # type: ignore[no-untyped-def]
    engine.answers = {key: list(value) for key, value in FORGET_OK.items()}
    chats.unlisted = True
    body = client.post("/api/v1/memory/forget", json={"confirm": "DELETE"}).json()

    assert body["failed_chat_sessions"] == 1
    assert body["checks"]["chat_history_cleared"] is False
