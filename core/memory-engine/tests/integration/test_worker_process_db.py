"""The worker as a **process**: a real ``scripts/run_worker.py`` child, real signals, a real database (``V2-D106``).

The loop is tested in-process in ``test_worker_loop_db.py``. What only a process test can show is the contract a
supervisor relies on — and that ``deploy/cme-worker.service`` and ``deploy/compose.worker.yaml`` are written against:

* the entry point starts from the environment alone and serves a queued job end to end;
* ``SIGTERM`` (what systemd's ``KillSignal`` and Docker's ``STOPSIGNAL`` send) ends it with **exit 0**, finishing the
  job in hand rather than abandoning its lease;
* ``SIGKILL`` mid-job (what a supervisor sends after ``TimeoutStopSec``, or what an OOM kill looks like) leaves a held
  lease, and a **restarted** process — the supervisor's ``Restart=always`` — returns it and completes the job;
* a missing required variable is exit 2 with the variable's name and never its value.

The model provider is a stub HTTP server speaking the OpenAI chat shape, started inside the test. It is the only fake:
the child process, its HTTP client, the queue, the role and the writes are all the deployed code.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from psycopg import AsyncConnection, sql
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from custom_memory_engine.queue.worker import enqueue
from custom_memory_engine.security import provision_runtime_role
from custom_memory_engine.semantic.slow_lane import enqueue_slow_lane
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
TEXT = "I live in Berlin"


@dataclass
class Provider:
    """The stub model: answers every extraction with one claim, after ``delay`` seconds."""

    port: int
    calls: list[str]
    delay: list[float]


@asynccontextmanager
async def provider() -> AsyncIterator[Provider]:
    calls: list[str] = []
    delay = [0.0]

    async def chat(request: Request) -> JSONResponse:
        body = await request.json()
        calls.append(str(body.get("model")))
        if delay[0]:
            await asyncio.sleep(delay[0])
        content = {
            "claims": [{"content": "The user lives in Berlin", "quote": TEXT, "kind": "claim"}],
            "language": "en",
        }
        return JSONResponse(
            {
                "model": body.get("model"),
                "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"])])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.01)
    try:
        yield Provider(server.servers[0].sockets[0].getsockname()[1], calls, delay)
    finally:
        # `force_exit`: a request the killed worker left hanging must not hold the stub open
        server.should_exit = True
        server.force_exit = True
        await asyncio.wait_for(task, timeout=10)


@dataclass
class Rig:
    owner: AsyncConnection
    schema: str
    env: dict[str, str]
    model: Provider

    async def enqueue(self, turn_id: str, sequence: int) -> None:
        await self.owner.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, 's-alice', 'alice', %s, 'user', %s, %s)",
            (turn_id, sequence, TEXT, T0),
        )
        await enqueue(
            self.owner,
            enqueue_slow_lane(
                job_id="job-" + turn_id,
                user_id="alice",
                turn_id=turn_id,
                extractor_version="stub/extract-v1",
                candidate_fingerprint="f" * 32,
            ),
            schema_name=self.schema,
        )
        await self.owner.commit()

    async def status(self, job_id: str) -> str | None:
        cursor = await self.owner.execute(sql.SQL("SELECT status FROM {} WHERE id = %s").format(
            sql.Identifier(self.schema, "job")), (job_id,))  # fmt: skip
        row = await cursor.fetchone()
        await self.owner.commit()
        return None if row is None else str(row[0])

    async def wait_for(self, job_id: str, wanted: str, *, seconds: float = 30) -> None:
        for _ in range(int(seconds / 0.1)):
            if await self.status(job_id) == wanted:
                return
            await asyncio.sleep(0.1)
        raise AssertionError(f"{job_id} never reached {wanted}; it is {await self.status(job_id)}")

    async def start(self, *extra: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            sys.executable,
            str(ROOT / "scripts" / "run_worker.py"),
            *extra,
            cwd=str(ROOT),
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )


@asynccontextmanager
async def rig(database) -> AsyncIterator[Rig]:  # noqa: F811
    config, schema = database
    role = "cme_rt_p_" + uuid4().hex[:10]
    async with connect(database) as owner, provider() as model:
        await owner.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (T0,))
        await owner.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', %s)", (T0,))
        await owner.execute("SELECT set_config('app.user_id', 'alice', false)")
        await provision_runtime_role(owner, schema, role)
        await owner.commit()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "CME_DATABASE_URL": config.database_url.get_secret_value(),
            "CME_RUNTIME_ROLE": role,
            "CME_SCHEMA": schema,
            "CME_WORKER_USERS": "alice",
            "CME_LLM_BASE_URL": f"http://127.0.0.1:{model.port}/v1",
            "CME_LLM_API_KEY": "stub-provider-key",  # pragma: allowlist secret
            "CME_LLM_PROTOCOL": "openai",
            "CME_EXTRACTOR_MODEL": "stub-extractor",
            "CME_ADJUDICATE": "0",
            "CME_WORKER_IDLE_POLL": "0.2",
        }
        try:
            yield Rig(owner, schema, env, model)
        finally:
            await owner.rollback()
            await owner.execute("RESET ROLE")
            await owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await owner.commit()


async def stop(process: asyncio.subprocess.Process, sig: int) -> int:
    """Send ``sig`` and wait for the exit code, bounded like the unit's ``TimeoutStopSec``."""
    process.send_signal(sig)
    return await asyncio.wait_for(process.wait(), timeout=30)


async def test_the_process_serves_a_job_and_exits_zero_on_sigterm(database) -> None:  # noqa: F811
    async with rig(database) as r:
        await r.enqueue("t-1", 1)
        process = await r.start()
        try:
            await r.wait_for("job-t-1", "done")
        finally:
            code = await stop(process, signal.SIGTERM)
        assert code == 0
        stderr = (await process.stderr.read()).decode() if process.stderr else ""  # type: ignore[union-attr]
        stdout = (await process.stdout.read()).decode() if process.stdout else ""  # type: ignore[union-attr]
        assert "slow_lane_stopped" in stdout + stderr
        # nothing the provider was given or returned is in the logs: the key, and the turn's text
        assert "stub-provider-key" not in stdout + stderr
        assert TEXT not in stdout + stderr
        assert r.model.calls == ["stub-extractor"]


async def test_sigterm_mid_job_finishes_the_job_before_exiting(database) -> None:  # noqa: F811
    """The graceful stop systemd and Docker rely on: the signal lands during the model call, the job still commits."""
    async with rig(database) as r:
        r.model.delay[0] = 1.5
        await r.enqueue("t-2", 1)
        process = await r.start()
        for _ in range(200):
            if r.model.calls:
                break
            await asyncio.sleep(0.05)
        assert r.model.calls, "the worker reached the model"
        code = await stop(process, signal.SIGTERM)
        assert code == 0
        assert await r.status("job-t-2") == "done"


async def test_a_killed_worker_leaves_a_lease_a_restarted_one_recovers(database) -> None:  # noqa: F811
    """SIGKILL cannot be handled — which is the point. The lease survives the process; the restart returns it.

    The lease is shortened by hand after the kill (a real one is ten minutes) so the test does not wait out the
    production window; everything else — the held ``running`` row, recovery on start, completion — is the real path.
    """
    async with rig(database) as r:
        r.model.delay[0] = 30.0  # the model hangs; the process is killed while waiting on it
        await r.enqueue("t-3", 1)
        process = await r.start()
        for _ in range(200):
            if r.model.calls:
                break
            await asyncio.sleep(0.05)
        await stop(process, signal.SIGKILL)
        assert await r.status("job-t-3") == "running", "a killed worker leaves its lease behind"
        await r.owner.execute(
            sql.SQL("UPDATE {} SET next_run_at = pg_catalog.now() - interval '1 second' WHERE id = 'job-t-3'").format(
                sql.Identifier(r.schema, "job")
            )
        )
        await r.owner.commit()
        r.model.delay[0] = 0.0
        restarted = await r.start()
        try:
            await r.wait_for("job-t-3", "done")
        finally:
            assert await stop(restarted, signal.SIGTERM) == 0


async def test_a_missing_variable_is_exit_two_and_names_the_variable_not_a_value(database) -> None:  # noqa: F811
    async with rig(database) as r:
        env = dict(r.env)
        del env["CME_RUNTIME_ROLE"]
        r.env = env
        process = await r.start()
        code = await asyncio.wait_for(process.wait(), timeout=30)
        stderr = (await process.stderr.read()).decode() if process.stderr else ""  # type: ignore[union-attr]
        assert code == 2
        assert "cme_runtime_role_required" in stderr
        assert r.env["CME_DATABASE_URL"] not in stderr
