"""Ownership: the engine suite owns engine semantics; this module owns the app-to-engine wire contract.

The engine's own end-to-end suite is ``core/memory-engine/tests/integration/test_host_e2e_db.py``. Every test here
drives the app's real ``MemoryEngineClient`` — ``initialize()``, ``add()``, ``search()`` — against the engine's real
HTTP host (``core/memory-engine/scripts/run_host.py``, run by the engine's own interpreter) on a real
PostgreSQL schema migrated and provisioned by the engine's own commands. Nothing is mocked, so a renamed envelope field,
a changed operation name, a changed ``query`` request or result shape, or a changed render expectation fails here even
when both projects' own suites (each against a mock of the other) are green.

What this module asserts is only what crosses the boundary: the envelope the host accepts (a delegating token naming
the end user in ``principal``), the ``{"result": ...}`` answer the client unwraps, the channel labels the client renders,
the cardinality and slot the client sends (``V2-D115``: Berlin then Paris renders one known fact), and that two end users
behind one service token never see each other's memory. How the engine ranks, supersedes or isolates is the engine
suite's to prove; it is observed here only through the rendered context.

Runs when ``core/memory-engine/.venv/bin/python`` and ``CME_TEST_DATABASE_URL`` both exist; otherwise it skips with
the reason. No model provider and no model files are needed: a lexical-only host answers the queries used here.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql

from app.core.config import settings
from app.services.memory_engine import MemoryEngineClient
from app.services.memory_slots import NullSlotClassifier, SlotExtraction
from tests.slot_fakes import current, recorded_classifier

ROOT = Path(__file__).resolve().parents[4]
ENGINE_DIRECTORY = ROOT / "services" / "memory-engine"
ENGINE_PYTHON = ENGINE_DIRECTORY / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
DATABASE_URL = os.environ.get("CME_TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(
        not ENGINE_PYTHON.exists(),
        reason="engine interpreter absent: run `uv sync --locked --extra host` in core/memory-engine/",
    ),
    pytest.mark.skipif(not DATABASE_URL, reason="CME_TEST_DATABASE_URL is not set (PostgreSQL with pgvector)"),
]

# The app's service token, configured on the host as a delegating token (V2-D120). Deliberately low-entropy: a test
# value a secret scanner has nothing to flag in.
SERVICE_TOKEN = "test-token-chronamind-app-delegating"  # pragma: allowlist secret
HOST_TOKENS = json.dumps({SERVICE_TOKEN: {"principal": "chronamind-app", "tier": "B", "delegate": True}})
PROVENANCE_KEY = "test-provenance-key-" + "p" * 24  # pragma: allowlist secret
DELETION_RECEIPT_KEY = "test-deletion-receipt-key-" + "d" * 24  # pragma: allowlist secret

KNOWN_FACTS = "Known facts"
EARLIER_CONVERSATION = "Earlier conversation (verbatim; may be outdated; not confirmed facts)"


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests on asyncio: the app's own event loop."""
    return "asyncio"


@dataclass(frozen=True)
class EngineHost:
    """A running engine host on a throwaway schema."""

    url: str
    schema: str


def _engine_environment(**overrides: str) -> dict[str, str]:
    """The inherited environment minus any engine or migration setting, plus ``overrides``.

    A developer's shell may export ``CME_*`` (an embedding asset, a judge) or ``RL_*``; none of it may leak into a host
    that is meant to be lexical-only and pointed at a throwaway schema.
    """
    inherited = {name: value for name, value in os.environ.items() if not name.startswith(("CME_", "RL_"))}
    return {**inherited, **overrides}


def _run_engine(arguments: list[str], environment: dict[str, str]) -> None:
    completed = subprocess.run(
        [str(ENGINE_PYTHON), *arguments],
        cwd=ENGINE_DIRECTORY,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"engine command {arguments[:2]} failed: {completed.stderr.strip()[-2000:]}")


#: Windows only: psycopg's async pool refuses the Proactor event loop uvicorn picks there, so the host is started on a
#: selector loop instead. The script, its environment and its configuration are the same; Linux and CI run it directly.
_WINDOWS_HOST_SHIM = """
import asyncio, runpy, sys, uvicorn
def run(app, **options):
    server = uvicorn.Server(uvicorn.Config(app, loop="none", **options))
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
uvicorn.run = run
sys.argv = ["scripts/run_host.py"]
runpy.run_path("scripts/run_host.py", run_name="__main__")
"""


def _host_command() -> list[str]:
    """The engine host's command line: ``scripts/run_host.py`` under the engine's interpreter."""
    if os.name == "nt":
        return [str(ENGINE_PYTHON), "-c", _WINDOWS_HOST_SHIM]
    return [str(ENGINE_PYTHON), "scripts/run_host.py"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_health(url: str, host: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if host.poll() is not None:
            pytest.fail(f"the engine host exited with {host.returncode}: {log.read_text(errors='replace')[-2000:]}")
        try:
            if httpx.get(f"{url}/healthz", timeout=1.0).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    pytest.fail(f"the engine host did not become healthy: {log.read_text(errors='replace')[-2000:]}")


def _drop_schema_and_role(schema: str, role: str) -> None:
    """Remove the throwaway runtime role and schema.

    Not ``migrate reset``: that command drops the managed objects and *re-installs* them (it resets, it does not
    remove), so it would leave a fresh schema behind on every run. The schema name is unique to this module's run.
    """
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is not None:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture(scope="module")
def engine_host(tmp_path_factory: pytest.TempPathFactory) -> Iterator[EngineHost]:
    """Migrate a throwaway schema, provision its runtime role, start the real host; remove all three afterwards."""
    suffix = uuid4().hex[:12]
    schema, role = f"cme_appe2e_{suffix}", f"cme_appe2e_rt_{suffix}"
    owner = _engine_environment(RL_DATABASE_URL=DATABASE_URL)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    log = tmp_path_factory.mktemp("engine-host") / "host.log"
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


class ObservedTransport(httpx.AsyncHTTPTransport):
    """A real network transport that also records ``(operation, status)`` for every exchange.

    ``add`` and ``search`` log and swallow engine failures by design (memory is an enhancement), so a refused call would
    otherwise surface only as a missing fact. Recording the statuses makes a contract break fail with the 4xx it caused.
    """

    def __init__(self) -> None:
        """A default real transport with an empty exchange log."""
        super().__init__()
        self.exchanges: list[tuple[str, int]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Send ``request`` over the network and record its operation and status."""
        response = await super().handle_async_request(request)
        operation = json.loads(request.content).get("operation", "?") if request.content else request.url.path
        self.exchanges.append((str(operation), response.status_code))
        return response


@pytest.fixture
def configured(engine_host: EngineHost, monkeypatch: pytest.MonkeyPatch) -> EngineHost:
    """Point the app's settings at the running host, exactly as a deployment's environment would."""
    monkeypatch.setattr(settings, "MEMORY_ENGINE_URL", engine_host.url, raising=False)
    monkeypatch.setattr(settings, "MEMORY_ENGINE_TOKEN", SERVICE_TOKEN, raising=False)
    return engine_host


def end_user() -> str:
    """A fresh end user, spelled as the app spells one (``str(session.user_id)``, a decimal integer).

    Delegation provisions it on first call, so no test shares a namespace with another.
    """
    return str(uuid4().int % 10**15)


def section(rendered: str, label: str) -> list[str]:
    """The ``* `` lines under ``label:`` in a rendered context, or ``[]`` when the label is absent."""
    lines: list[str] = []
    inside = False
    for line in rendered.splitlines():
        if line.endswith(":") and not line.startswith("* "):
            inside = line == f"{label}:"
        elif inside and line.startswith("* "):
            lines.append(line[2:])
    return lines


def assert_all_accepted(transport: ObservedTransport) -> None:
    refused = [(operation, status) for operation, status in transport.exchanges if status != 200]
    assert not refused, f"the engine host refused the app's requests: {refused}"


# ------------------------------------------------------------------------------------------------ the round trip


async def test_initialize_add_and_search_round_trip_through_the_real_host(configured: EngineHost) -> None:
    transport = ObservedTransport()
    client = MemoryEngineClient(transport=transport)
    user = end_user()
    try:
        await client.initialize()
        await client.add(
            user,
            [
                {"role": "user", "content": "I keep bees in Lisbon"},
                {"role": "assistant", "content": "Beekeeping is a lovely hobby"},
            ],
        )
        rendered = await client.search(user, "bees")
        assistant = await client.search(user, "Beekeeping")
    finally:
        await client.aclose()

    assert_all_accepted(transport)
    # the pre-warm, both turns recorded, only the user's turn asserted, then the two queries
    assert transport.exchanges == [
        ("/healthz", 200),
        ("ingest_episode", 200),
        ("assert", 200),
        ("ingest_episode", 200),
        ("query", 200),
        ("query", 200),
    ]
    assert section(rendered, KNOWN_FACTS) == ["I keep bees in Lisbon"]
    # the claim and the turn it is grounded in share one text: rendered once, as the fact
    assert rendered.count("I keep bees in Lisbon") == 1
    # an assistant's turn is recorded, and is never presented as a known fact
    assert section(assistant, KNOWN_FACTS) == []
    assert section(assistant, EARLIER_CONVERSATION) == ["Beekeeping is a lovely hobby"]


# ---------------------------------------------------------------------------------- cardinality (V2-D115, #248)


#: Recorded model answers for every statement below. Each still passes the app's own checks (``decide``): the value is
#: grounded in the message, in its own script. What a real model answers for these messages is measured by
#: ``tests/live/test_memory_slots_live.py``; this file proves what the engine does with the answer.
ANSWERS: dict[str, SlotExtraction] = {
    "I live in Berlin": current("residence", "Berlin"),
    "I moved to Paris": current("residence", "Paris"),
    "أنا ساكن في برلين": current("residence", "برلين"),
    "انتقلت إلى باريس": current("residence", "باريس"),
    "رجعت أعيش في برلين": current("residence", "برلين"),
    "I live in Berlin, بس دلوقتي انتقلت لـ Paris": current("residence", "Paris"),
}


async def test_a_move_from_berlin_to_paris_renders_one_known_fact(configured: EngineHost) -> None:
    """The product-level regression for #248: a changed residence is never rendered as two known facts."""
    transport = ObservedTransport()
    classifier, _ = recorded_classifier(ANSWERS)
    client = MemoryEngineClient(transport=transport, slot_classifier=classifier)
    user = end_user()
    try:
        await client.add(user, [{"role": "user", "content": "I live in Berlin"}])
        before = await client.search(user, "Berlin")
        await client.add(user, [{"role": "user", "content": "I moved to Paris"}])
        berlin = await client.search(user, "Berlin")
        paris = await client.search(user, "Paris")
    finally:
        await client.aclose()

    assert_all_accepted(transport)
    assert section(before, KNOWN_FACTS) == ["I live in Berlin"]
    # after the move the Berlin claim is superseded: it is no longer a known fact under any query, and the Berlin turn
    # survives only as earlier conversation
    assert section(berlin, KNOWN_FACTS) == []
    assert section(berlin, EARLIER_CONVERSATION) == ["I live in Berlin"]
    assert section(paris, KNOWN_FACTS) == ["I moved to Paris"]
    assert "Berlin" not in "\n".join(section(berlin, KNOWN_FACTS) + section(paris, KNOWN_FACTS))


@pytest.mark.parametrize(
    ("statements", "berlin_query", "paris_query"),
    [
        (("I live in Berlin", "I moved to Paris", "I live in Berlin"), "Berlin", "Paris"),
        (("أنا ساكن في برلين", "انتقلت إلى باريس", "رجعت أعيش في برلين"), "برلين", "باريس"),
    ],
    ids=["english", "arabic"],
)
async def test_a_return_to_an_earlier_value_wins_the_slot_back_across_whole_history_resends(
    configured: EngineHost, statements: tuple[str, str, str], berlin_query: str, paris_query: str
) -> None:
    """The agent's real shape: every turn resends the session's whole history to ``add``, in any language.

    A resend must replay (no duplicate claim), and a user who says they live in Berlin again after moving to Paris must
    win the slot back. With text-only idempotency keys the second Berlin statement replayed the first, superseded
    claim, and Paris stayed the current fact.
    """
    transport = ObservedTransport()
    classifier, model = recorded_classifier(ANSWERS)
    client = MemoryEngineClient(transport=transport, slot_classifier=classifier)
    user = end_user()
    history: list[dict[str, str]] = []
    try:
        for said in statements:
            history += [{"role": "user", "content": said}, {"role": "assistant", "content": "Noted."}]
            await client.add(user, list(history), session_id="session-1")
        berlin = await client.search(user, berlin_query)
        paris = await client.search(user, paris_query)
    finally:
        await client.aclose()

    assert_all_accepted(transport)
    # one model call per turn: the resent history is never re-classified
    assert model.asked == list(statements)
    assert section(berlin, KNOWN_FACTS) == [statements[2]]
    assert statements[1] not in section(paris, KNOWN_FACTS)


async def test_a_mixed_language_correction_supersedes_the_slot(configured: EngineHost) -> None:
    """Arabic and English in one message: the grounded value is the Latin-script one the user actually wrote."""
    transport = ObservedTransport()
    classifier, _ = recorded_classifier(ANSWERS)
    client = MemoryEngineClient(transport=transport, slot_classifier=classifier)
    user = end_user()
    corrected = "I live in Berlin, بس دلوقتي انتقلت لـ Paris"
    try:
        await client.add(user, [{"role": "user", "content": "أنا ساكن في برلين"}])
        await client.add(user, [{"role": "user", "content": corrected}])
        arabic = await client.search(user, "برلين")
        paris = await client.search(user, "Paris")
    finally:
        await client.aclose()

    assert_all_accepted(transport)
    assert section(arabic, KNOWN_FACTS) == []
    assert section(paris, KNOWN_FACTS) == [corrected]


async def test_without_a_slot_the_same_move_leaves_both_known_facts(configured: EngineHost) -> None:
    """The control for the tests above: the slot the app sends is what makes the difference, not the query.

    With no semantic classifier (every claim ``("multi", None)``, the app's behaviour before ``V2-D115``) the identical
    sequence keeps Berlin as a known fact after the move.
    """
    transport = ObservedTransport()
    client = MemoryEngineClient(transport=transport, slot_classifier=NullSlotClassifier())
    user = end_user()
    try:
        await client.add(user, [{"role": "user", "content": "I live in Berlin"}])
        await client.add(user, [{"role": "user", "content": "I moved to Paris"}])
        berlin = await client.search(user, "Berlin")
        paris = await client.search(user, "Paris")
    finally:
        await client.aclose()

    assert_all_accepted(transport)
    assert section(berlin, KNOWN_FACTS) == ["I live in Berlin"]
    assert section(paris, KNOWN_FACTS) == ["I moved to Paris"]


# ------------------------------------------------------------------------------------------------------ isolation


async def test_two_end_users_behind_one_service_token_never_see_each_others_memory(configured: EngineHost) -> None:
    transport = ObservedTransport()
    client = MemoryEngineClient(transport=transport)
    alice, bob = end_user(), end_user()
    try:
        await client.add(alice, [{"role": "user", "content": "I keep falcons in Granada"}])
        await client.add(bob, [{"role": "user", "content": "I keep goats in Tromso"}])
        alice_own = await client.search(alice, "falcons")
        bob_reads_alice = await client.search(bob, "falcons")
        bob_own = await client.search(bob, "goats")
        alice_reads_bob = await client.search(alice, "goats")
    finally:
        await client.aclose()

    assert_all_accepted(transport)
    assert section(alice_own, KNOWN_FACTS) == ["I keep falcons in Granada"]
    assert section(bob_own, KNOWN_FACTS) == ["I keep goats in Tromso"]
    # not hidden behind a label: nothing of the other user is rendered at all
    assert bob_reads_alice == ""
    assert alice_reads_bob == ""
