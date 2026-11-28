"""Stop the ChronaMind live demo stack (processes and database containers).

Usage (from the repository root):
    uv run --project apps/api python tools/demo/down.py          # stop stack; keep volumes and .env.demo keys
    uv run --project apps/api python tools/demo/down.py --wipe   # stop stack; remove volumes, .env.demo, and .state/
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

os.environ.pop("SSLKEYLOGFILE", None)

HERE = Path(__file__).resolve().parent
STATE = HERE / ".state"
ENV_FILE = HERE / ".env.demo"
COMPOSE_PROJECT = "chronamind-demo"
STACK_PORTS: tuple[tuple[str, int], ...] = (("app", 8000), ("engine_host", 8100))


def port_listening(host: str, port: int) -> bool:
    """Return True if a TCP listener accepts connections on ``(host, port)``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` currently exists."""
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


_EXPECTED_CMD_MARKER: dict[str, str] = {
    "engine_host": "run_host.py",
    "app": "app.main:app",
}


def _pid_matches_service(pid: int, service: str | None) -> bool:
    """Guard against PID reuse by checking the live command line when readable."""
    if service is None:
        return True
    marker = _EXPECTED_CMD_MARKER.get(service)
    if not marker:
        return True
    if os.name == "nt":
        ps_bin = shutil.which("powershell") or shutil.which("pwsh")
        if not ps_bin:
            return True
        result = subprocess.run(
            [
                ps_bin,
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if result.returncode != 0:
            return True
        cmdline = result.stdout.strip()
        return bool(cmdline) and marker in cmdline
    result = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return False
    return marker in result.stdout


def stop_pid(pid: int, *, service: str | None = None, timeout: float = 15.0) -> None:
    """Terminate a process (and its process group/tree) and wait until it exits."""
    if not _pid_alive(pid) or not _pid_matches_service(pid, service):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError:
            os.kill(pid, signal.SIGKILL)


def _parse_env_file(env_file: Path) -> dict[str, str]:
    """Read simple ``KEY=VALUE`` pairs from ``env_file``."""
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def compose_environment(env_file: Path, *, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the Docker Compose environment so ``.env.demo`` overrides inherited shell variables."""
    source = os.environ if base_env is None else base_env
    overrides = _parse_env_file(env_file) if env_file.exists() else {}
    return {**source, **overrides}


def run_docker_down(*, compose_file: Path, env_file: Path, wipe: bool) -> None:
    """Stop the demo Docker Compose containers, removing volumes when ``wipe`` is True."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        if wipe:
            raise RuntimeError(
                "Docker CLI not found on PATH; refusing to delete .env.demo while database volumes cannot be removed."
            )
        return
    command = [docker_bin, "compose", "-p", COMPOSE_PROJECT]
    if compose_file.exists():
        command.extend(["-f", str(compose_file)])
    if env_file.exists():
        command.extend(["--env-file", str(env_file)])
    command.append("down")
    if wipe:
        command.append("-v")
    completed = subprocess.run(
        command,
        env=compose_environment(env_file),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"docker compose down failed (exit {completed.returncode})")


def _load_pids(pids_file: Path) -> dict[str, dict[str, int]]:
    """Read tracked process entries from ``pids_file``."""
    if not pids_file.exists():
        return {}
    try:
        raw = json.loads(pids_file.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except ValueError, OSError:
        return {}


def _wait_ports_closed(ports: list[int], timeout: float = 10.0) -> list[int]:
    """Wait until none of ``ports`` accept TCP connections; return any still open."""
    deadline = time.monotonic() + timeout
    remaining = list(ports)
    while remaining and time.monotonic() < deadline:
        remaining = [port for port in remaining if port_listening("127.0.0.1", port)]
        if remaining:
            time.sleep(0.1)
    return remaining


def teardown(*, state_dir: Path = STATE, env_file: Path = ENV_FILE, wipe: bool = False) -> int:
    """Stop stack processes and containers; remove volumes and secrets when ``wipe`` is True."""
    pids_file = state_dir / "pids.json"
    pids = _load_pids(pids_file)
    stopped_ports: list[int] = []

    # Stop app before engine host so the app closes its engine client cleanly.
    ordered_names = [name for name, _ in STACK_PORTS if name in pids] + [
        name for name in pids if name not in {n for n, _ in STACK_PORTS}
    ]
    for name in ordered_names:
        entry = pids[name]
        pid = int(entry.get("pid", 0))
        port = int(entry.get("port", 0))
        if pid > 0:
            stop_pid(pid, service=name)
        if port > 0:
            stopped_ports.append(port)

    still_open = _wait_ports_closed(sorted({*stopped_ports, *(port for _, port in STACK_PORTS)}))
    if pids_file.exists():
        pids_file.unlink()

    run_docker_down(compose_file=state_dir / "compose.yaml", env_file=env_file, wipe=wipe)

    if wipe:
        if env_file.exists():
            env_file.unlink()
        if state_dir.exists():
            shutil.rmtree(state_dir)

    if still_open:
        print(f"demo stack teardown incomplete: port(s) still listening: {still_open}", file=sys.stderr)
        return 1

    if wipe:
        print("demo stack stopped and wiped (volumes, .env.demo, and .state removed)")
    else:
        print("demo stack stopped (volumes and .env.demo preserved)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for stopping the demo stack."""
    parser = argparse.ArgumentParser(description="Stop the ChronaMind demo stack.")
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Also remove Docker database volumes, tools/demo/.env.demo, and tools/demo/.state/.",
    )
    args = parser.parse_args(argv)
    try:
        return teardown(state_dir=STATE, env_file=ENV_FILE, wipe=args.wipe)
    except Exception as error:
        print(f"demo stack teardown failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
