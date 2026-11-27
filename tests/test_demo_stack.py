"""Tests for the one-command live demo stack (`scripts/demo/`)."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "scripts" / "demo"


def load_demo_module(name: str) -> ModuleType:
    """Load one script from ``scripts/demo/`` (which is not a package)."""
    path = DEMO_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"demo_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_secrets_generates_once_and_reuses_across_runs(tmp_path: Path) -> None:
    up = load_demo_module("up")
    env_file = tmp_path / ".env.demo"

    first = up.demo_secrets(env_file)
    second = up.demo_secrets(env_file)

    assert first == second
    assert first["MEMORY_ENGINE_URL"] == "http://127.0.0.1:8100"
    assert first["RATE_LIMIT_REGISTER"] == "1000 per hour"
    for key in (
        "MEMORY_ENGINE_TOKEN",
        "MEMORY_ENGINE_USER_TOKEN",
        "CME_PROVENANCE_KEY",
        "CME_DELETION_RECEIPT_KEY",
        "JWT_SECRET_KEY",
    ):
        assert len(first[key]) >= 32
    assert first["MEMORY_ENGINE_TOKEN"] != first["MEMORY_ENGINE_USER_TOKEN"]
    assert first["CME_PROVENANCE_KEY"] != first["CME_DELETION_RECEIPT_KEY"]
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_engine_environment_strips_inherited_cme_rl_and_sslkeylogfile_and_sets_two_delegating_tokens(
    tmp_path: Path,
) -> None:
    up = load_demo_module("up")
    values = up.demo_secrets(tmp_path / ".env.demo")
    base_env = {
        "PATH": "/usr/bin",
        "CME_EMBEDDING_ASSET": "/should/be/dropped",
        "RL_DATABASE_URL": "postgresql://wrong",
        "SSLKEYLOGFILE": r"\\.\aswMonFltProxy\keylog",
    }

    env = up.engine_environment("postgresql://cme_demo:pw@127.0.0.1:5441/cme_demo", values, base_env=base_env)

    assert "CME_EMBEDDING_ASSET" not in env
    assert "SSLKEYLOGFILE" not in env
    assert env["CME_SCHEMA"] == "cme_demo"
    assert env["CME_RUNTIME_ROLE"] == "cme_demo_rt"
    assert env["CME_PROVENANCE_KEY"] == values["CME_PROVENANCE_KEY"]
    assert env["CME_DELETION_RECEIPT_KEY"] == values["CME_DELETION_RECEIPT_KEY"]
    assert env["CME_HOST_HOST"] == "127.0.0.1"
    assert env["CME_HOST_PORT"] == "8100"

    tokens = json.loads(env["CME_HOST_TOKENS"])
    assert tokens == {
        values["MEMORY_ENGINE_TOKEN"]: {"principal": "chronamind-app", "tier": "B", "delegate": True},
        values["MEMORY_ENGINE_USER_TOKEN"]: {"principal": "chronamind-inspector", "tier": "C", "delegate": True},
    }


def test_app_environment_sets_demo_vars_and_keeps_logs_in_state(tmp_path: Path) -> None:
    up = load_demo_module("up")
    values = up.demo_secrets(tmp_path / ".env.demo")
    state_dir = tmp_path / ".state"
    base_env = {
        "PATH": "/usr/bin",
        "SSLKEYLOGFILE": r"\\.\aswMonFltProxy\keylog",
        "OPENAI_API_KEY": "sk-user-key",  # pragma: allowlist secret
    }

    env = up.app_environment(values, state_dir=state_dir, base_env=base_env)

    assert "SSLKEYLOGFILE" not in env
    assert env["OPENAI_API_KEY"] == "sk-user-key"  # pragma: allowlist secret
    assert env["MEMORY_ENGINE_URL"] == values["MEMORY_ENGINE_URL"]
    assert env["MEMORY_ENGINE_TOKEN"] == values["MEMORY_ENGINE_TOKEN"]
    assert env["MEMORY_ENGINE_USER_TOKEN"] == values["MEMORY_ENGINE_USER_TOKEN"]
    assert env["RATE_LIMIT_REGISTER"] == "1000 per hour"
    assert env["POSTGRES_HOST"] == "127.0.0.1"
    assert env["POSTGRES_PORT"] == values["POSTGRES_PORT"]
    assert env["LOG_DIR"] == str(state_dir / "logs")
    assert env["PROFILING_DIR"] == str(state_dir / "profiles")


@pytest.mark.parametrize("secret_key", ["MEMORY_ENGINE_TOKEN", "MEMORY_ENGINE_USER_TOKEN", "CME_PROVENANCE_KEY"])
def test_compose_manifest_never_embeds_raw_secrets(tmp_path: Path, secret_key: str) -> None:
    up = load_demo_module("up")
    values = up.demo_secrets(tmp_path / ".env.demo")
    manifest = up.render_compose()

    assert values[secret_key] not in manifest
    assert values["DEMO_ENGINE_DB_PASSWORD"] not in manifest
    assert values["POSTGRES_PASSWORD"] not in manifest
    assert "${DEMO_ENGINE_DB_PASSWORD}" in manifest
    assert "${POSTGRES_PASSWORD}" in manifest


def test_down_preserves_env_file_and_volumes_without_wipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up = load_demo_module("up")
    down = load_demo_module("down")
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    env_file = tmp_path / ".env.demo"
    values = up.demo_secrets(env_file)
    pids_file = state_dir / "pids.json"
    pids_file.write_text(
        json.dumps({"app": {"pid": 11111, "port": 8000}, "engine_host": {"pid": 22222, "port": 8100}})
    )
    (state_dir / "compose.yaml").write_text(up.render_compose(), encoding="utf-8")

    stopped: list[int] = []
    docker_calls: list[list[str]] = []
    monkeypatch.setattr(down, "stop_pid", lambda pid, **kw: stopped.append(pid))
    monkeypatch.setattr(down, "run_docker_down", lambda **kw: docker_calls.append(["-v"] if kw["wipe"] else []))
    monkeypatch.setattr(down, "port_listening", lambda host, port: False)

    rc = down.teardown(state_dir=state_dir, env_file=env_file, wipe=False)

    assert rc == 0
    assert set(stopped) == {11111, 22222}
    assert docker_calls == [[]]
    assert not pids_file.exists()
    assert env_file.exists()
    assert up.demo_secrets(env_file)["CME_PROVENANCE_KEY"] == values["CME_PROVENANCE_KEY"]
    assert up.demo_secrets(env_file)["CME_DELETION_RECEIPT_KEY"] == values["CME_DELETION_RECEIPT_KEY"]


def test_down_with_wipe_removes_volumes_env_file_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up = load_demo_module("up")
    down = load_demo_module("down")
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    env_file = tmp_path / ".env.demo"
    up.demo_secrets(env_file)
    (state_dir / "pids.json").write_text(json.dumps({"app": {"pid": 11111, "port": 8000}}))
    (state_dir / "compose.yaml").write_text(up.render_compose(), encoding="utf-8")

    docker_calls: list[bool] = []
    monkeypatch.setattr(down, "stop_pid", lambda pid, **kw: None)
    monkeypatch.setattr(down, "run_docker_down", lambda **kw: docker_calls.append(bool(kw["wipe"])))
    monkeypatch.setattr(down, "port_listening", lambda host, port: False)

    rc = down.teardown(state_dir=state_dir, env_file=env_file, wipe=True)

    assert rc == 0
    assert docker_calls == [True]
    assert not env_file.exists()
    assert not state_dir.exists()


def test_check_passes_when_paris_supersedes_berlin_and_ignores_episodes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    check = load_demo_module("check")
    calls: list[tuple[str, str, object]] = []
    query_answers = iter(
        [
            {"facts": [{"kind": "episode", "content": "I live in Berlin"}]},
            {
                "facts": [
                    {"kind": "episode", "content": "I live in Berlin"},
                    {"kind": "claim", "content": "I live in Berlin"},
                ]
            },
            {
                "facts": [
                    {"kind": "episode", "content": "I live in Berlin"},
                    {"kind": "episode", "content": "I moved to Paris"},
                    {"kind": "claim", "content": "I moved to Paris"},
                ]
            },
        ]
    )

    def fake_request(
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, object]:
        del timeout
        calls.append((method, url, json_body))
        if url.endswith("/api/v1/auth/register"):
            return 200, {"id": 42, "email": "u@example.com", "token": {"access_token": "user-jwt-secret"}}
        if url.endswith("/api/v1/auth/session"):
            assert token == "user-jwt-secret"
            return 200, {"session_id": "sess-1", "token": {"access_token": "session-jwt-secret"}}
        if url.endswith("/api/v1/chatbot/chat"):
            assert token == "session-jwt-secret"
            return 200, {"messages": [{"role": "assistant", "content": "Got it."}]}
        if url.endswith("/v1/call"):
            assert token == "inspector-token-secret"
            assert json_body is not None and json_body["operation"] == "query"
            assert json_body["principal"] == "42"
            return 200, {"result": next(query_answers)}
        raise AssertionError(f"unexpected request: {method} {url}")

    ok = check.run_check(
        app_url="http://127.0.0.1:8000",
        engine_url="http://127.0.0.1:8100",
        engine_token="inspector-token-secret",
        request_fn=fake_request,
        timeout_seconds=5.0,
        poll_interval=0.0,
    )

    captured = capsys.readouterr()
    assert ok is True
    assert "inspector-token-secret" not in captured.out + captured.err
    assert "user-jwt-secret" not in captured.out + captured.err
    assert "session-jwt-secret" not in captured.out + captured.err
    chat_messages = [
        body["messages"][0]["content"]  # type: ignore[index]
        for _, url, body in calls
        if url.endswith("/api/v1/chatbot/chat") and isinstance(body, dict)
    ]
    assert chat_messages == ["I live in Berlin", "I moved to Paris"]


def test_check_fails_when_both_berlin_and_paris_remain_as_non_episode_facts() -> None:
    check = load_demo_module("check")
    state = {"turn": 0}

    def fake_request(
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, object]:
        del method, json_body, token, timeout
        if url.endswith("/api/v1/auth/register"):
            return 200, {"id": 99, "token": {"access_token": "u"}}
        if url.endswith("/api/v1/auth/session"):
            return 200, {"session_id": "s", "token": {"access_token": "st"}}
        if url.endswith("/api/v1/chatbot/chat"):
            state["turn"] += 1
            return 200, {"messages": [{"role": "assistant", "content": "ok"}]}
        if url.endswith("/v1/call"):
            if state["turn"] == 1:
                return 200, {"result": {"facts": [{"kind": "claim", "content": "I live in Berlin"}]}}
            return 200, {
                "result": {
                    "facts": [
                        {"kind": "claim", "content": "I live in Berlin"},
                        {"kind": "claim", "content": "I moved to Paris"},
                    ]
                }
            }
        raise AssertionError(url)

    ok = check.run_check(
        app_url="http://127.0.0.1:8000",
        engine_url="http://127.0.0.1:8100",
        engine_token="tok",
        request_fn=fake_request,
        timeout_seconds=0.05,
        poll_interval=0.01,
    )
    assert ok is False


def test_up_rolls_back_started_processes_and_prints_no_secrets_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    up = load_demo_module("up")
    state_dir = tmp_path / ".state"
    env_file = tmp_path / ".env.demo"
    values = up.demo_secrets(env_file)

    monkeypatch.setattr(up, "STATE", state_dir)
    monkeypatch.setattr(up, "ENV_FILE", env_file)
    monkeypatch.setattr(up, "PIDS_FILE", state_dir / "pids.json")
    monkeypatch.setattr(up, "_check_prerequisites", lambda: None)
    monkeypatch.setattr(up, "_port_listening", lambda host, port: False)
    monkeypatch.setattr(up, "start_databases", lambda s, e, **kw: None)
    monkeypatch.setattr(up, "prepare_engine", lambda env, s, **kw: None)
    monkeypatch.setattr(up, "prepare_app_db", lambda env, s, e, **kw: None)

    class FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def poll(self) -> None:
            return None

    spawned = iter([FakeProc(7001), FakeProc(7002)])
    monkeypatch.setattr(up, "_spawn_detached", lambda *a, **kw: next(spawned))

    def fake_wait(url: str, proc: object, log_file: Path, *, name: str, timeout: float = 90.0) -> None:
        del url, proc, log_file, timeout
        if name == "app":
            raise RuntimeError("app failed to boot")

    stopped: list[int] = []
    monkeypatch.setattr(up, "_wait_for_health", fake_wait)
    monkeypatch.setattr(up, "_stop_pid", lambda pid: stopped.append(pid))

    rc = up.main()

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert rc == 1
    assert stopped == [7002, 7001]
    for secret_key in (
        "MEMORY_ENGINE_TOKEN",
        "MEMORY_ENGINE_USER_TOKEN",
        "CME_PROVENANCE_KEY",
        "CME_DELETION_RECEIPT_KEY",
        "DEMO_ENGINE_DB_PASSWORD",
        "POSTGRES_PASSWORD",
        "JWT_SECRET_KEY",
    ):
        assert values[secret_key] not in output


def test_demo_gitignore_ignores_env_and_state() -> None:
    lines = {line.strip() for line in (DEMO_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert ".env.demo" in lines
    assert ".state/" in lines


def test_prepare_engine_skips_provision_role_when_role_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    up = load_demo_module("up")
    ran: list[str] = []
    monkeypatch.setattr(up, "_run_logged", lambda cmd, **kw: ran.append(kw["step"]))

    monkeypatch.setattr(up, "engine_role_exists", lambda dsn, role: False)
    up.prepare_engine({"RL_DATABASE_URL": "postgresql://u:p@127.0.0.1:5441/cme_demo"}, tmp_path)
    assert ran == ["engine schema migrate", "engine role provision"]

    ran.clear()
    monkeypatch.setattr(up, "engine_role_exists", lambda dsn, role: True)
    up.prepare_engine({"RL_DATABASE_URL": "postgresql://u:p@127.0.0.1:5441/cme_demo"}, tmp_path)
    assert ran == ["engine schema migrate"]


def test_check_main_uses_tier_b_agent_token_never_tier_c_inspector_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    up = load_demo_module("up")
    check = load_demo_module("check")
    env_file = tmp_path / ".env.demo"
    values = up.demo_secrets(env_file)
    seen: dict[str, str] = {}

    def fake_run_check(*, app_url: str, engine_url: str, engine_token: str) -> bool:
        seen["app_url"] = app_url
        seen["engine_url"] = engine_url
        seen["engine_token"] = engine_token
        return True

    monkeypatch.setattr(check, "ENV_FILE", env_file)
    monkeypatch.setattr(check, "run_check", fake_run_check)

    assert check.main() == 0
    assert seen["engine_token"] == values["MEMORY_ENGINE_TOKEN"]
    assert seen["engine_token"] != values["MEMORY_ENGINE_USER_TOKEN"]


def test_up_rolls_back_and_clears_pids_on_keyboard_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up = load_demo_module("up")
    state_dir = tmp_path / ".state"
    env_file = tmp_path / ".env.demo"
    pids_file = state_dir / "pids.json"
    up.demo_secrets(env_file)

    monkeypatch.setattr(up, "STATE", state_dir)
    monkeypatch.setattr(up, "ENV_FILE", env_file)
    monkeypatch.setattr(up, "PIDS_FILE", pids_file)
    monkeypatch.setattr(up, "_check_prerequisites", lambda: None)
    monkeypatch.setattr(up, "_port_listening", lambda host, port: False)
    monkeypatch.setattr(up, "start_databases", lambda s, e, **kw: None)
    monkeypatch.setattr(up, "prepare_engine", lambda env, s, **kw: None)
    monkeypatch.setattr(up, "prepare_app_db", lambda env, s, e, **kw: None)

    class FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    monkeypatch.setattr(up, "_spawn_detached", lambda *a, **kw: FakeProc(8101))

    def interrupt_wait(*a: object, **kw: object) -> None:
        del a, kw
        assert pids_file.exists(), "PID must be recorded immediately after spawn"
        raise KeyboardInterrupt

    stopped: list[int] = []
    monkeypatch.setattr(up, "_wait_for_health", interrupt_wait)
    monkeypatch.setattr(up, "_stop_pid", lambda pid: stopped.append(pid))

    rc = up.main()

    assert rc == 130
    assert stopped == [8101]
    assert not pids_file.exists()


def test_down_fails_when_stack_port_still_listening_without_pid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    down = load_demo_module("down")
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    env_file = tmp_path / ".env.demo"
    env_file.write_text("KEY=val\n", encoding="utf-8")
    monkeypatch.setattr(down, "run_docker_down", lambda **kw: None)
    monkeypatch.setattr(down, "port_listening", lambda host, port: port == 8100)
    monkeypatch.setattr(down, "_wait_ports_closed", lambda ports, timeout=10.0: [8100])

    rc = down.teardown(state_dir=state_dir, env_file=env_file, wipe=True)

    assert rc == 1
    assert not env_file.exists()
    assert not state_dir.exists()


def test_wait_for_health_backs_off_on_http_non_200_to_avoid_health_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    up = load_demo_module("up")
    statuses = iter([0, 503, 200])
    sleeps: list[float] = []

    class AliveProc:
        returncode = None

        def poll(self) -> None:
            return None

    monkeypatch.setattr(up, "_probe_http_status", lambda url, timeout=2.0: next(statuses))
    monkeypatch.setattr(up.time, "sleep", lambda seconds: sleeps.append(seconds))

    up._wait_for_health("http://127.0.0.1:8000/health", AliveProc(), tmp_path / "app.log", name="app", timeout=30.0)

    assert sleeps == [0.25, 3.0, 0.1]


def test_stop_pid_skips_signaling_when_pid_belongs_to_unrelated_process(monkeypatch: pytest.MonkeyPatch) -> None:
    down = load_demo_module("down")
    signaled: list[int] = []
    monkeypatch.setattr(down, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(down, "_pid_matches_service", lambda pid, service: False)
    monkeypatch.setattr(down.os, "killpg", lambda pid, sig: signaled.append(pid), raising=False)

    down.stop_pid(99999, service="app")

    assert signaled == []
