"""Start the ChronaMind live demo stack: engine DB, engine host, app DB, and app.

Usage (from the repository root):
    uv run python scripts/demo/up.py

All generated secrets live in ``scripts/demo/.env.demo`` and runtime state/logs live
in ``scripts/demo/.state/`` (both gitignored). No token, key, password, or database
URL is printed to the console or written outside those two paths.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote_plus

import psycopg

# Avast on Windows sets SSLKEYLOGFILE to a minifilter path that aborts Python's ssl
# module with OPENSSL_Uplink (even on plain HTTP calls through urllib).
os.environ.pop("SSLKEYLOGFILE", None)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENGINE = ROOT / "custom-memory-engine"
ENGINE_PYTHON = ENGINE / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
STATE = HERE / ".state"
ENV_FILE = HERE / ".env.demo"
PIDS_FILE = STATE / "pids.json"
COMPOSE_PROJECT = "chronamind-demo"
STARTUP_BUDGET_SECONDS = 180.0

SCHEMA = "cme_demo"
ROLE = "cme_demo_rt"
APP_HOST = "127.0.0.1"
APP_PORT = 8000
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 8100
DEFAULT_ENGINE_DB_PORT = "5441"
DEFAULT_APP_DB_PORT = "5442"
ENGINE_DB_IMAGE = (
    "pgvector/pgvector:0.8.7-pg16-bookworm@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"
)
TIKTOKEN_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"

#: Keys written into ``scripts/demo/.env.demo`` that must override ``.env.development`` in the app process.
APP_OVERRIDE_KEYS: tuple[str, ...] = (
    "MEMORY_ENGINE_URL",
    "MEMORY_ENGINE_TOKEN",
    "MEMORY_ENGINE_USER_TOKEN",
    "RATE_LIMIT_REGISTER",
    "JWT_SECRET_KEY",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "LANGFUSE_TRACING_ENABLED",
    "DEBUG",
)

#: Windows only: psycopg's async pool refuses uvicorn's default Proactor event loop.
_WINDOWS_HOST_SHIM = """
import asyncio, runpy, sys, uvicorn
def _run(app, **options):
    server = uvicorn.Server(uvicorn.Config(app, loop="none", **options))
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
uvicorn.run = _run
sys.argv = ["scripts/run_host.py"]
runpy.run_path("scripts/run_host.py", run_name="__main__")
""".strip()

#: Shared bootstrap for app-side subprocesses (uvicorn and Alembic): reads ``.env.demo`` from disk
#: (never argv) and wraps ``dotenv.load_dotenv`` so demo overrides stay authoritative even when
#: ``.env.development`` exists (``app/core/config.py`` calls ``load_dotenv(..., override=True)``).
_DEMO_ENV_BOOTSTRAP = """
import os, sys
from pathlib import Path
import dotenv

_env_path = Path(sys.argv[1])
_override_keys = set(sys.argv[2].split(","))
_demo_vars = {
    k.strip(): v
    for line in _env_path.read_text(encoding="utf-8").splitlines()
    if "=" in line and not line.lstrip().startswith("#")
    for k, v in (line.split("=", 1),)
    if k.strip() in _override_keys
}
_demo_vars["LOG_DIR"] = sys.argv[3]
_orig_load_dotenv = dotenv.load_dotenv

def _demo_load_dotenv(*args, **kwargs):
    result = _orig_load_dotenv(*args, **kwargs)
    os.environ.update(_demo_vars)
    return result

dotenv.load_dotenv = _demo_load_dotenv
os.environ.update(_demo_vars)
""".strip()

_APP_LAUNCHER = (
    _DEMO_ENV_BOOTSTRAP
    + "\n"
    + """
import asyncio, uvicorn
_demo_vars["PROFILING_DIR"] = sys.argv[4]
os.environ["PROFILING_DIR"] = sys.argv[4]
_server = uvicorn.Server(uvicorn.Config("app.main:app", host=sys.argv[5], port=int(sys.argv[6]), loop="none"))
asyncio.run(_server.serve(), loop_factory=asyncio.SelectorEventLoop)
""".strip()
)

_ALEMBIC_LAUNCHER = (
    _DEMO_ENV_BOOTSTRAP
    + "\n"
    + """
from alembic.config import main as alembic_main
alembic_main(argv=["upgrade", "head"])
""".strip()
)


def _loopback_opener() -> urllib.request.OpenerDirector:
    """Build an opener that bypasses system HTTP proxies for local loopback requests."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def parse_env_file(env_file: Path) -> dict[str, str]:
    """Read simple ``KEY=VALUE`` pairs from ``env_file``."""
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def _write_env_file(env_file: Path, values: Mapping[str, str]) -> None:
    """Write ``values`` to ``env_file`` with owner-only permissions on POSIX."""
    env_file.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{key}={value}\n" for key, value in values.items())
    if os.name == "nt":
        env_file.write_text(content, encoding="utf-8")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(env_file, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    finally:
        os.chmod(env_file, 0o600)


def demo_secrets(env_file: Path = ENV_FILE) -> dict[str, str]:
    """Generate demo tokens, keys, and settings once; reuse them on every subsequent run."""
    existing = parse_env_file(env_file) if env_file.exists() else {}
    defaults: dict[str, str] = {
        "MEMORY_ENGINE_URL": f"http://{ENGINE_HOST}:{ENGINE_PORT}",
        "MEMORY_ENGINE_TOKEN": secrets.token_urlsafe(32),
        "MEMORY_ENGINE_USER_TOKEN": secrets.token_urlsafe(32),
        "CME_PROVENANCE_KEY": secrets.token_urlsafe(32),
        "CME_DELETION_RECEIPT_KEY": secrets.token_urlsafe(32),
        "RATE_LIMIT_REGISTER": "1000 per hour",
        "JWT_SECRET_KEY": secrets.token_hex(32),
        "LANGFUSE_TRACING_ENABLED": "false",
        # Avoid ProfilingMiddleware (pyinstrument + tracemalloc), which stalls async chat on CPython 3.14 when installed.
        "DEBUG": "false",
        "DEMO_ENGINE_DB_USER": "cme_demo",
        "DEMO_ENGINE_DB_NAME": "cme_demo",
        "DEMO_ENGINE_DB_PORT": DEFAULT_ENGINE_DB_PORT,
        "DEMO_ENGINE_DB_PASSWORD": secrets.token_urlsafe(24),
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": DEFAULT_APP_DB_PORT,
        "POSTGRES_DB": "chronamind_demo",
        "POSTGRES_USER": "chronamind_demo",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
    }
    merged = dict(existing)
    changed = not env_file.exists()
    for key, value in defaults.items():
        if not merged.get(key):
            merged[key] = value
            changed = True
    if changed:
        _write_env_file(env_file, merged)
    return merged


def engine_database_url(values: Mapping[str, str]) -> str:
    """Build the owner PostgreSQL DSN for the demo engine database."""
    user = quote_plus(values["DEMO_ENGINE_DB_USER"])
    password = quote_plus(values["DEMO_ENGINE_DB_PASSWORD"])
    port = values["DEMO_ENGINE_DB_PORT"]
    database = quote_plus(values["DEMO_ENGINE_DB_NAME"])
    return f"postgresql://{user}:{password}@127.0.0.1:{port}/{database}"


def engine_environment(
    database_url: str,
    values: Mapping[str, str],
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the engine host environment: strip inherited CME_/RL_/SSLKEYLOGFILE, add demo config."""
    source = os.environ if base_env is None else base_env
    inherited = {k: v for k, v in source.items() if not k.startswith(("CME_", "RL_")) and k != "SSLKEYLOGFILE"}
    tokens = {
        values["MEMORY_ENGINE_TOKEN"]: {"principal": "chronamind-app", "tier": "B", "delegate": True},
        values["MEMORY_ENGINE_USER_TOKEN"]: {"principal": "chronamind-inspector", "tier": "C", "delegate": True},
    }
    return {
        **inherited,
        "RL_DATABASE_URL": database_url,
        "CME_DATABASE_URL": database_url,
        "CME_RUNTIME_ROLE": ROLE,
        "CME_SCHEMA": SCHEMA,
        "CME_HOST_TOKENS": json.dumps(tokens),
        "CME_PROVENANCE_KEY": values["CME_PROVENANCE_KEY"],
        "CME_DELETION_RECEIPT_KEY": values["CME_DELETION_RECEIPT_KEY"],
        "CME_HOST_HOST": ENGINE_HOST,
        "CME_HOST_PORT": str(ENGINE_PORT),
    }


def app_environment(
    values: Mapping[str, str],
    *,
    state_dir: Path = STATE,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the FastAPI app environment with demo settings and state-scoped logs."""
    source = os.environ if base_env is None else base_env
    env = {k: v for k, v in source.items() if k != "SSLKEYLOGFILE"}
    for key in APP_OVERRIDE_KEYS:
        env[key] = values[key]
    env["LOG_DIR"] = str(state_dir / "logs")
    env["PROFILING_DIR"] = str(state_dir / "profiles")
    return env


def render_compose() -> str:
    """Return the Docker Compose YAML for the engine and app databases (secrets come from ``.env.demo``)."""
    return f"""services:
  engine-db:
    image: {ENGINE_DB_IMAGE}
    environment:
      POSTGRES_USER: ${{DEMO_ENGINE_DB_USER}}
      POSTGRES_PASSWORD: ${{DEMO_ENGINE_DB_PASSWORD}}
      POSTGRES_DB: ${{DEMO_ENGINE_DB_NAME}}
    ports:
      - "127.0.0.1:${{DEMO_ENGINE_DB_PORT}}:5432"
    volumes:
      - engine-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 2s
      timeout: 3s
      retries: 30

  app-db:
    image: {ENGINE_DB_IMAGE}
    environment:
      POSTGRES_USER: ${{POSTGRES_USER}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
      POSTGRES_DB: ${{POSTGRES_DB}}
    ports:
      - "127.0.0.1:${{POSTGRES_PORT}}:5432"
    volumes:
      - app-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 2s
      timeout: 3s
      retries: 30

volumes:
  engine-db-data:
  app-db-data:
"""


def _probe_http_status(url: str, timeout: float = 2.0) -> int:
    """Return the HTTP status code from ``GET url``, or ``0`` on connection/transport errors."""
    request = urllib.request.Request(url, method="GET")
    try:
        with _loopback_opener().open(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except urllib.error.URLError, TimeoutError, OSError:
        return 0


def _check_http_ok(url: str, timeout: float = 2.0) -> bool:
    """Return True iff ``GET url`` responds with HTTP 200."""
    return _probe_http_status(url, timeout=timeout) == 200


def _port_listening(host: str, port: int) -> bool:
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
    """Guard against PID reuse on POSIX by checking the live command line when readable."""
    if service is None or os.name == "nt":
        return True
    marker = _EXPECTED_CMD_MARKER.get(service)
    if not marker:
        return True
    result = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return False
    return marker in result.stdout


def _stop_pid(pid: int, *, service: str | None = None, timeout: float = 15.0) -> None:
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


def _seed_windows_tiktoken_cache(state_dir: Path) -> Path:
    """Best-effort curl download of tiktoken encoding for Windows behind TLS-inspecting proxies."""
    cache_dir = state_dir / "tiktoken_cache"
    target = cache_dir / hashlib.sha1(TIKTOKEN_URL.encode("utf-8")).hexdigest()
    if target.exists() and target.stat().st_size > 0:
        return cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    curl_bin = shutil.which("curl")
    if curl_bin:
        subprocess.run(
            [curl_bin, "-sS", "-m", "90", "-o", str(target), TIKTOKEN_URL], capture_output=True, check=False
        )
    return cache_dir


def _build_windows_ca_shim(state_dir: Path) -> Path | None:
    """Build a certifi + Windows store CA bundle shim for Avast/corporate TLS proxies on Windows."""
    if sys.platform != "win32":
        return None
    try:
        import certifi  # pyright: ignore[reportMissingImports]
    except ImportError:
        return None
    shim_dir = state_dir / "shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    bundle = shim_dir / "ca-bundle.pem"
    parts = [Path(certifi.where()).read_text(encoding="utf-8")]
    enum_certs = getattr(ssl, "enum_certificates", None)
    if enum_certs is not None:
        for store in ("ROOT", "CA"):
            for der, encoding, _trust in enum_certs(store):
                if encoding == "x509_asn":
                    body = base64.encodebytes(der).decode("ascii")
                    parts.append(f"-----BEGIN CERTIFICATE-----\n{body}-----END CERTIFICATE-----\n")
    bundle.write_text("\n".join(parts), encoding="utf-8")
    (shim_dir / "sitecustomize.py").write_text(
        f"import certifi\ncertifi.where = lambda: {str(bundle)!r}\n",
        encoding="utf-8",
    )
    return shim_dir


def _run_logged(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_file: Path,
    step: str,
    timeout: float = 180.0,
) -> None:
    """Run a command capturing all output into ``log_file`` so no secrets or DSNs reach stdout/stderr."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("wb") as output:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env),
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"{step} failed (exit {completed.returncode}); see {log_file}")


def _check_prerequisites() -> None:
    """Verify Docker and the engine interpreter exist before starting anything."""
    if not ENGINE_PYTHON.exists():
        raise RuntimeError(
            "Engine interpreter not found. Run `uv sync --locked --extra host` in `custom-memory-engine/`."
        )
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise RuntimeError("Docker CLI not found on PATH. Install and start Docker first.")
    info = subprocess.run([docker_bin, "info"], capture_output=True, check=False)
    if info.returncode != 0:
        raise RuntimeError("Docker daemon is not reachable. Start Docker and retry.")


def compose_environment(env_file: Path, *, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the Docker Compose environment so ``.env.demo`` overrides inherited shell variables."""
    source = os.environ if base_env is None else base_env
    overrides = parse_env_file(env_file) if env_file.exists() else {}
    return {**source, **overrides}


def start_databases(state_dir: Path = STATE, env_file: Path = ENV_FILE, *, timeout: float = 180.0) -> None:
    """Start the demo engine and app PostgreSQL containers and wait until both are healthy."""
    state_dir.mkdir(parents=True, exist_ok=True)
    compose_path = state_dir / "compose.yaml"
    compose_path.write_text(render_compose(), encoding="utf-8")
    _run_logged(
        [
            "docker",
            "compose",
            "-p",
            COMPOSE_PROJECT,
            "-f",
            str(compose_path),
            "--env-file",
            str(env_file),
            "up",
            "-d",
            "--wait",
        ],
        cwd=ROOT,
        env=compose_environment(env_file),
        log_file=state_dir / "docker-up.log",
        step="docker compose up",
        timeout=timeout,
    )


def engine_role_exists(database_url: str, role: str) -> bool:
    """Return True if PostgreSQL role ``role`` already exists on the engine database."""
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        row = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        return row is not None


def prepare_engine(environment: Mapping[str, str], state_dir: Path = STATE, *, timeout: float = 120.0) -> None:
    """Migrate the demo schema and provision its runtime role using the engine's interpreter."""
    _run_logged(
        [str(ENGINE_PYTHON), "-m", "custom_memory_engine.schema.migrate", "apply", "--schema", SCHEMA],
        cwd=ENGINE,
        env=environment,
        log_file=state_dir / "engine-schema-migrate.log",
        step="engine schema migrate",
        timeout=timeout,
    )
    # `provision_role.py` runs `CREATE ROLE` without `IF NOT EXISTS` and raises `DuplicateObject`
    # when the role already exists on a reused volume; use `down.py --wipe` to rebuild grants.
    if not engine_role_exists(environment["RL_DATABASE_URL"], ROLE):
        _run_logged(
            [str(ENGINE_PYTHON), "scripts/provision_role.py", "--schema", SCHEMA, "--role", ROLE],
            cwd=ENGINE,
            env=environment,
            log_file=state_dir / "engine-role-provision.log",
            step="engine role provision",
            timeout=timeout,
        )


def prepare_app_db(
    environment: Mapping[str, str],
    state_dir: Path = STATE,
    env_file: Path = ENV_FILE,
    *,
    timeout: float = 120.0,
) -> None:
    """Run Alembic migrations against the demo app database."""
    _run_logged(
        [
            sys.executable,
            "-c",
            _ALEMBIC_LAUNCHER,
            str(env_file),
            ",".join(APP_OVERRIDE_KEYS),
            str(state_dir / "logs"),
        ],
        cwd=ROOT,
        env=environment,
        log_file=state_dir / "alembic.log",
        step="alembic upgrade head",
        timeout=timeout,
    )


def _host_command() -> list[str]:
    """Return the engine host command line (using a Selector loop shim on Windows)."""
    if os.name == "nt":
        return [str(ENGINE_PYTHON), "-c", _WINDOWS_HOST_SHIM]
    return [str(ENGINE_PYTHON), "scripts/run_host.py"]


def _app_command(env_file: Path, state_dir: Path) -> list[str]:
    """Return the app launcher command line (reading demo overrides from ``env_file``)."""
    return [
        sys.executable,
        "-c",
        _APP_LAUNCHER,
        str(env_file),
        ",".join(APP_OVERRIDE_KEYS),
        str(state_dir / "logs"),
        str(state_dir / "profiles"),
        APP_HOST,
        str(APP_PORT),
    ]


def _spawn_detached(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_file: Path,
) -> subprocess.Popen[bytes]:
    """Start a detached background process whose stdout/stderr go to ``log_file``."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": dict(env),
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with log_file.open("wb") as output:
        return subprocess.Popen(command, stdout=output, **kwargs)  # type: ignore[call-overload]


def _wait_for_health(
    url: str,
    process: subprocess.Popen[bytes],
    log_file: Path,
    *,
    name: str,
    timeout: float = 90.0,
) -> None:
    """Poll ``url`` until it answers HTTP 200 or ``process`` exits.

    Connection-refused probes retry every 0.25s (the server is still binding and does not hit rate
    limits); once HTTP responds non-200 (e.g. 503), back off to 3.0s so ``/health`` (20/min) is
    never tripped into 429.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited early (code {process.returncode}); see {log_file}")
        status = _probe_http_status(url, timeout=2.0)
        if status == 200:
            # Confirm the spawned process itself is still alive (guards against an unrelated listener).
            time.sleep(0.1)
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited early (code {process.returncode}); see {log_file}")
            return
        time.sleep(0.25 if status == 0 else 3.0)
    raise RuntimeError(f"{name} did not become healthy within {int(timeout)}s; see {log_file}")


def _load_pids(pids_file: Path = PIDS_FILE) -> dict[str, dict[str, int]]:
    """Read tracked process entries from ``pids_file``."""
    if not pids_file.exists():
        return {}
    try:
        raw = json.loads(pids_file.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except ValueError, OSError:
        return {}


def _save_pids(pids: Mapping[str, Mapping[str, int]], pids_file: Path = PIDS_FILE) -> None:
    """Persist tracked process entries into ``pids_file``."""
    pids_file.parent.mkdir(parents=True, exist_ok=True)
    pids_file.write_text(json.dumps(pids, indent=2) + "\n", encoding="utf-8")


def _remaining_budget(deadline: float, cap: float) -> float:
    """Return remaining seconds until ``deadline`` (capped at ``cap``), raising if expired."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError(f"startup exceeded the {int(STARTUP_BUDGET_SECONDS)}s budget")
    return min(remaining, cap)


def main() -> int:
    """Start databases, migrate schemas, boot the engine host and app, and wait until healthy."""
    deadline = time.monotonic() + STARTUP_BUDGET_SECONDS
    started_pids: list[int] = []
    tracked_pids: dict[str, dict[str, int]] = {}
    try:
        _check_prerequisites()
        STATE.mkdir(parents=True, exist_ok=True)
        values = demo_secrets(ENV_FILE)

        host_health_url = f"http://{ENGINE_HOST}:{ENGINE_PORT}/healthz"
        app_health_url = f"http://{APP_HOST}:{APP_PORT}/health"

        existing_pids = _load_pids(PIDS_FILE)
        host_recorded = existing_pids.get("engine_host", {}).get("pid", 0)
        app_recorded = existing_pids.get("app", {}).get("pid", 0)
        if (
            _pid_alive(host_recorded)
            and _pid_alive(app_recorded)
            and _check_http_ok(host_health_url)
            and _check_http_ok(app_health_url)
        ):
            print(f"all healthy: app {app_health_url} (200), host {host_health_url} (200)")
            return 0

        # Clean up any stale tracked processes before starting fresh ones.
        for service, entry in existing_pids.items():
            pid = int(entry.get("pid", 0))
            if _pid_alive(pid):
                _stop_pid(pid, service=service)

        for label, host, port in (("engine host", ENGINE_HOST, ENGINE_PORT), ("app", APP_HOST, APP_PORT)):
            if _port_listening(host, port):
                raise RuntimeError(
                    f"Port {port} ({label}) is already in use by another process. Stop it or run down.py first."
                )

        print("Starting demo databases...")
        start_databases(STATE, ENV_FILE, timeout=_remaining_budget(deadline, 180.0))

        engine_env = engine_environment(engine_database_url(values), values)
        print("Preparing engine schema and runtime role...")
        prepare_engine(engine_env, STATE, timeout=_remaining_budget(deadline, 120.0))

        app_env = app_environment(values, state_dir=STATE)
        if sys.platform == "win32":
            app_env["TIKTOKEN_CACHE_DIR"] = str(_seed_windows_tiktoken_cache(STATE))
            shim = _build_windows_ca_shim(STATE)
            if shim is not None:
                existing_pypath = app_env.get("PYTHONPATH", "")
                app_env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(shim), existing_pypath]))

        print("Running app database migrations...")
        prepare_app_db(app_env, STATE, ENV_FILE, timeout=_remaining_budget(deadline, 120.0))

        print(f"Starting engine host on {ENGINE_HOST}:{ENGINE_PORT}...")
        host_log = STATE / "engine_host.log"
        host_proc = _spawn_detached(_host_command(), cwd=ENGINE, env=engine_env, log_file=host_log)
        started_pids.append(host_proc.pid)
        tracked_pids["engine_host"] = {"pid": host_proc.pid, "port": ENGINE_PORT}
        _save_pids(tracked_pids, PIDS_FILE)
        _wait_for_health(
            host_health_url,
            host_proc,
            host_log,
            name="engine host",
            timeout=_remaining_budget(deadline, 60.0),
        )

        print(f"Starting app on {APP_HOST}:{APP_PORT}...")
        app_log = STATE / "app.log"
        app_proc = _spawn_detached(_app_command(ENV_FILE, STATE), cwd=ROOT, env=app_env, log_file=app_log)
        started_pids.append(app_proc.pid)
        tracked_pids["app"] = {"pid": app_proc.pid, "port": APP_PORT}
        _save_pids(tracked_pids, PIDS_FILE)
        _wait_for_health(
            app_health_url,
            app_proc,
            app_log,
            name="app",
            timeout=_remaining_budget(deadline, 90.0),
        )

        print(f"all healthy: app {app_health_url} (200), host {host_health_url} (200)")
        return 0
    except (KeyboardInterrupt, Exception) as error:
        for pid in reversed(started_pids):
            _stop_pid(pid)
        if started_pids and PIDS_FILE.exists():
            PIDS_FILE.unlink()
        if isinstance(error, KeyboardInterrupt):
            print("demo stack startup interrupted", file=sys.stderr)
            return 130
        print(f"demo stack startup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
