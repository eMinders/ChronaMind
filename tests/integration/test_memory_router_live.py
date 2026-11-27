"""The memory inspector routes against the engine's real HTTP host and PostgreSQL. Nothing is mocked but the user.

`tests/test_memory_router.py` proves what the routes send and how they map answers, against a scripted host. This
module proves the same routes against the real one, so a changed result shape, a mode the engine refuses, or a tier the
token does not carry fails here instead of in the interface. The host is configured the way a deployment is: the
agent's Tier B delegating token writes memory through `MemoryEngineClient.add` (the agent's own path), and the
inspector's Tier C delegating token serves the routes (`V2-D120`).

Runs when the engine's interpreter (`custom-memory-engine/.venv`, `uv sync --locked --extra host`) and
`CME_TEST_DATABASE_URL` both exist; otherwise it skips with the reason. CI's `app-engine-boundary` job provides both and
fails on a skip. No model provider and no model files are needed: a lexical-only host answers everything used here.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

import app.api.v1.memory as memory_routes
from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.main import app
from app.models.user import User
from app.services.memory_engine import MemoryEngineClient, _claim_arguments, canonical_hash
from app.services.memory_slots import MULTI, NullSlotClassifier
from tests.integration.test_app_engine_boundary import (
    ANSWERS,
    DATABASE_URL,
    DELETION_RECEIPT_KEY,
    ENGINE_DIRECTORY,
    ENGINE_PYTHON,
    PROVENANCE_KEY,
    EngineHost,
    _drop_schema_and_role,
    _engine_environment,
    _free_port,
    _host_command,
    _run_engine,
    _wait_for_health,
)
from tests.slot_fakes import recorded_classifier

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(
        not ENGINE_PYTHON.exists(),
        reason="engine interpreter absent: run `uv sync --locked --extra host` in custom-memory-engine/",
    ),
    pytest.mark.skipif(not DATABASE_URL, reason="CME_TEST_DATABASE_URL is not set (PostgreSQL with pgvector)"),
]

# Both of the app's delegating service tokens. Deliberately low-entropy test values.
AGENT_TOKEN = "test-token-chronamind-agent-tier-b"  # pragma: allowlist secret
INSPECTOR_TOKEN = "test-token-chronamind-inspector-tier-c"  # pragma: allowlist secret
HOST_TOKENS = json.dumps(
    {
        AGENT_TOKEN: {"principal": "chronamind-app", "tier": "B", "delegate": True},
        INSPECTOR_TOKEN: {"principal": "chronamind-inspector", "tier": "C", "delegate": True},
    }
)
#: A pause around each recorded instant, so an instant falls strictly between two writes on the database clock.
GAP_SECONDS = 0.05


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests on asyncio: the app's own event loop."""
    return "asyncio"


@pytest.fixture(scope="module")
def inspector_host(tmp_path_factory: pytest.TempPathFactory) -> Iterator[EngineHost]:
    """A real host on a throwaway schema, holding both delegating tokens; removed afterwards."""
    suffix = uuid4().hex[:12]
    schema, role = f"cme_inspector_{suffix}", f"cme_inspector_rt_{suffix}"
    owner = _engine_environment(RL_DATABASE_URL=DATABASE_URL)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    log: Path = tmp_path_factory.mktemp("inspector-host") / "host.log"
    host: subprocess.Popen[bytes] | None = None
    try:
        _run_engine(["-m", "custom_memory_engine.schema.migrate", "apply", "--schema", schema], owner)
        _run_engine(["scripts/provision_role.py", "--schema", schema, "--role", role], owner)
        with log.open("wb") as output:
            host = subprocess.Popen(
                _host_command(),
                cwd=ENGINE_DIRECTORY,
                env=_engine_environment(
                    CME_DATABASE_URL=DATABASE_URL,
                    CME_RUNTIME_ROLE=role,
                    CME_SCHEMA=schema,
                    CME_HOST_TOKENS=HOST_TOKENS,
                    CME_PROVENANCE_KEY=PROVENANCE_KEY,
                    CME_DELETION_RECEIPT_KEY=DELETION_RECEIPT_KEY,
                    CME_HOST_HOST="127.0.0.1",
                    CME_HOST_PORT=str(port),
                ),
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        _wait_for_health(url, host, log)
        yield EngineHost(url=url, schema=schema)
    finally:
        if host is not None:
            host.terminate()
            try:
                host.wait(timeout=20)
            except subprocess.TimeoutExpired:
                host.kill()
                host.wait(timeout=20)
        _drop_schema_and_role(schema, role)


class NoChats:
    """The app's chat store, empty: this job has no app database, and Forget's session clearing is unit-tested."""

    async def get_user_sessions(self, user_id: int) -> list[Any]:
        """No sessions for anyone."""
        del user_id
        return []


@pytest.fixture
async def routes(inspector_host: EngineHost, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    """The application's own routes, pointed at the live host, with a fresh inspector client on this event loop."""
    monkeypatch.setattr(settings, "MEMORY_ENGINE_URL", inspector_host.url, raising=False)
    monkeypatch.setattr(settings, "MEMORY_ENGINE_TOKEN", AGENT_TOKEN, raising=False)
    monkeypatch.setattr(settings, "MEMORY_ENGINE_USER_TOKEN", INSPECTOR_TOKEN, raising=False)
    inspector = MemoryEngineClient(
        slot_classifier=NullSlotClassifier(), token_setting="MEMORY_ENGINE_USER_TOKEN", read_timeout=15.0
    )
    monkeypatch.setattr(memory_routes, "memory_inspector", inspector)
    monkeypatch.setattr(memory_routes, "database_service", NoChats())
    limiter.reset()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app.test") as client:
            yield client
    finally:
        await inspector.aclose()
        app.dependency_overrides.pop(get_current_user, None)


def act_as(user_id: int) -> None:
    """Make the routes see ``user_id`` as the signed-in user (the token check itself is the auth module's concern)."""
    app.dependency_overrides[get_current_user] = lambda: User(id=user_id, email=f"u{user_id}@example.com",
                                                              hashed_password="x")  # fmt: skip


def new_user() -> int:
    """A fresh end user, spelled as the app spells one: a positive integer id."""
    return uuid4().int % 10**12 + 1


async def tell(user_id: int, *statements: str) -> None:
    """Write memory exactly as the agent does: `MemoryEngineClient.add`, Tier B token, recorded slot answers."""
    classifier, _ = recorded_classifier(ANSWERS)
    agent_client = MemoryEngineClient(slot_classifier=classifier)
    try:
        for statement in statements:
            await agent_client.add(str(user_id), [{"role": "user", "content": statement}])
    finally:
        await agent_client.aclose()


async def instant() -> datetime:
    """Now, strictly between the write before it and the write after it."""
    await asyncio.sleep(GAP_SECONDS)
    moment = datetime.now(UTC)
    await asyncio.sleep(GAP_SECONDS)
    return moment


async def hedged_claim(user_id: int, text: str) -> str:
    """A claim the gate holds (`assertion_mode: hedged`); returns its id from the `assert` receipt.

    A held claim is `contested`, but `query` does not deliver it in any mode until it is adjudicated: it is readable by
    id only (`API_V2.md` §4.2, "an artifact still staged at `conflict_pending` is readable by id even though
    `current_state` does not yet deliver it"). So the id the receipt carries is the only handle on it.
    """
    inspector = MemoryEngineClient(slot_classifier=NullSlotClassifier(), token_setting="MEMORY_ENGINE_USER_TOKEN")
    turn_id = f"turn-{uuid4().hex[:16]}"
    scope = f"/u/{user_id}/"
    source = {"type": "external", "ref": f"app://session/{turn_id}", "span": text[:64],
              "content_hash": canonical_hash(text), "role": "user"}  # fmt: skip
    try:
        await inspector.call(str(user_id), "ingest_episode", {"scope": scope, "content": text, "source": source,
                                                              "event_time": None,
                                                              "idempotency_key": f"episode:{turn_id}"})  # fmt: skip
        arguments = _claim_arguments(scope, text, turn_id, MULTI)
        arguments["assertion_mode"] = "hedged"
        receipt = await inspector.call(str(user_id), "assert", arguments)
    finally:
        await inspector.aclose()
    assert receipt["effect"] == "contested"
    return str(receipt["id"])


def contents(items: list[dict[str, Any]]) -> list[str]:
    return [item["content"] for item in items]


# ------------------------------------------------------------------------------------------------ reading


async def test_the_list_shows_one_current_residence_and_keeps_both_turns_as_conversation(routes) -> None:  # type: ignore[no-untyped-def]
    user = new_user()
    await tell(user, "I live in Berlin", "I moved to Paris")
    act_as(user)

    response = await routes.get("/api/v1/memory/")

    assert response.status_code == 200, response.text
    body = response.json()
    assert contents(body["known_facts"]) == ["I moved to Paris"]
    assert sorted(contents(body["conversation"])) == ["I live in Berlin", "I moved to Paris"]
    assert "scope_path" not in response.text


async def test_an_item_and_its_provenance_point_at_the_users_own_message(routes) -> None:  # type: ignore[no-untyped-def]
    user = new_user()
    await tell(user, "I moved to Paris")
    act_as(user)
    (paris,) = (await routes.get("/api/v1/memory/")).json()["known_facts"]

    item = await routes.get(f"/api/v1/memory/{paris['id']}")
    provenance = await routes.get(f"/api/v1/memory/{paris['id']}/provenance", params={"depth": 1})

    assert item.status_code == 200, item.text
    assert (item.json()["lifecycle"], item.json()["channel"]) == ("asserted", "facts")
    assert any(source["readable"] and source["ref"].startswith("app://session/") for source in item.json()["sources"])
    assert provenance.status_code == 200, provenance.text


async def test_another_users_item_is_not_found_exactly_like_a_missing_one(routes) -> None:  # type: ignore[no-untyped-def]
    owner, stranger = new_user(), new_user()
    await tell(owner, "I moved to Paris")
    act_as(owner)
    (paris,) = (await routes.get("/api/v1/memory/")).json()["known_facts"]

    act_as(stranger)
    foreign = await routes.get(f"/api/v1/memory/{paris['id']}")
    missing = await routes.get("/api/v1/memory/v-does-not-exist")

    assert (foreign.status_code, foreign.json()["detail"]["code"]) == (404, "forbidden")
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "forbidden")


async def test_search_finds_the_current_fact(routes) -> None:  # type: ignore[no-untyped-def]
    user = new_user()
    await tell(user, "I live in Berlin", "I moved to Paris")
    act_as(user)

    response = await routes.post("/api/v1/memory/search", json={"text": "Paris"})

    assert response.status_code == 200, response.text
    assert "I moved to Paris" in contents(response.json()["known_facts"])


# ------------------------------------------------------------------------------------------------ time


async def test_the_timeline_shows_what_was_believed_at_each_instant(routes) -> None:  # type: ignore[no-untyped-def]
    user = new_user()
    t0 = await instant()
    await tell(user, "I live in Berlin")
    t1 = await instant()
    await tell(user, "I moved to Paris")
    t2 = await instant()
    act_as(user)

    before = (await routes.get("/api/v1/memory/timeline", params={"at": t0.isoformat()})).json()
    then = (await routes.get("/api/v1/memory/timeline", params={"at": t1.isoformat()})).json()
    now = (await routes.get("/api/v1/memory/timeline", params={"at": t2.isoformat()})).json()

    assert before["beliefs"] == []
    assert contents(then["beliefs"]) == ["I live in Berlin"]
    assert contents(now["beliefs"]) == ["I moved to Paris"]


async def test_changes_report_the_move_with_both_values(routes) -> None:  # type: ignore[no-untyped-def]
    user = new_user()
    await tell(user, "I live in Berlin")
    t1 = await instant()
    await tell(user, "I moved to Paris")
    t2 = await instant()
    act_as(user)

    response = await routes.get("/api/v1/memory/changes", params={"start": t1.isoformat(), "end": t2.isoformat()})

    assert response.status_code == 200, response.text
    changes = response.json()["changes"]
    assert changes, "the move from Berlin to Paris must appear as a change"
    assert any("I moved to Paris" in contents(change["current"]) for change in changes)
    # The outcome word the interface shows for this move (recorded for WEB-09).
    print("observed change outcomes:", sorted({change["outcome"] for change in changes}))


# ------------------------------------------------------------------------------------------------ adjudicate


async def test_a_held_claim_is_readable_by_id_and_listed_nowhere_until_adjudicated(routes) -> None:  # type: ignore[no-untyped-def]
    """The engine's rule the interface has to design around (`API_V2.md` §4.2)."""
    user = new_user()
    held = await hedged_claim(user, "I might take up the oud")
    act_as(user)

    item = await routes.get(f"/api/v1/memory/{held}")
    listed = (await routes.get("/api/v1/memory/")).json()

    assert item.status_code == 200, item.text
    assert (item.json()["lifecycle"], item.json()["channel"]) == ("contested", "conflicts")
    assert held not in [entry["id"] for entry in [*listed["known_facts"], *listed["conflicts"]]]


async def test_only_the_users_confirmation_turns_a_held_claim_into_a_known_fact(routes) -> None:  # type: ignore[no-untyped-def]
    user = new_user()
    held = await hedged_claim(user, "I might switch to a vegetarian diet")
    act_as(user)

    confirmed = await routes.post(f"/api/v1/memory/{held}/adjudicate", json={"decision": "confirm"})
    again = await routes.post(f"/api/v1/memory/{held}/adjudicate", json={"decision": "confirm"})
    after = (await routes.get("/api/v1/memory/")).json()

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["effect"] == "asserted"
    assert "I might switch to a vegetarian diet" in contents(after["known_facts"])
    assert (again.status_code, again.json()["detail"]["code"]) == (409, "precondition_failed")


async def test_a_denied_claim_is_retired_and_listed_nowhere(routes) -> None:  # type: ignore[no-untyped-def]
    user = new_user()
    held = await hedged_claim(user, "I might move to Rome")
    act_as(user)

    denied = await routes.post(f"/api/v1/memory/{held}/adjudicate", json={"decision": "deny"})
    after = (await routes.get("/api/v1/memory/")).json()

    assert denied.status_code == 200, denied.text
    assert denied.json()["effect"] == "retired"
    claims = [*after["known_facts"], *after["hypotheses"], *after["procedures"], *after["conflicts"]]
    assert "I might move to Rome" not in contents(claims)


# ------------------------------------------------------------------------------------------------ forget


async def test_forget_erases_everything_and_every_after_check_holds(routes) -> None:  # type: ignore[no-untyped-def]
    user = new_user()
    await tell(user, "I live in Berlin", "I moved to Paris")
    act_as(user)

    response = await routes.post("/api/v1/memory/forget", json={"confirm": "DELETE"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["receipt"]["deleted_versions"] > 0
    assert body["checks"] == {"memory_list_empty": True, "export_artifact_count": 0, "old_item_not_found": True}

    agent_client = MemoryEngineClient(slot_classifier=NullSlotClassifier())
    try:
        assert await agent_client.search(str(user), "Where do I live?") == ""
    finally:
        await agent_client.aclose()


async def test_the_agents_tier_b_token_cannot_erase(routes, inspector_host: EngineHost) -> None:  # type: ignore[no-untyped-def]
    """The reason the inspector holds its own token: Tier B never deletes (`TB1`)."""
    del routes
    agent_client = MemoryEngineClient(slot_classifier=NullSlotClassifier())
    try:
        with pytest.raises(Exception) as refused:
            await agent_client.call(str(new_user()), "delete", {"scope": "all"})
    finally:
        await agent_client.aclose()
    assert getattr(refused.value, "code", None) == "forbidden"
