"""Driver for the ChronaMind FastAPI app: start/stop the server and smoke-test its API.

Usage (from the repo root):
    uv run python .claude/skills/run-chronamind/driver.py up      # start detached server on :8765
    uv run python .claude/skills/run-chronamind/driver.py smoke   # health -> register -> login -> session -> chat -> messages
    uv run python .claude/skills/run-chronamind/driver.py down    # stop the server
    uv run python .claude/skills/run-chronamind/driver.py status  # print /health

Stdlib only. Workarounds for a Windows host with Avast are applied in `up` (see SKILL.md Gotchas).
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# Avast sets SSLKEYLOGFILE to a minifilter path; any ssl.create_default_context() then aborts with OPENSSL_Uplink,
# and urllib builds one even for plain http, so drop it before the first request.
os.environ.pop("SSLKEYLOGFILE", None)

NL = chr(10)
PORT = int(os.environ.get("CHRONA_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / ".claude" / "skills" / "run-chronamind" / ".state"
PID_FILE = STATE / "server.pid"
LOG_FILE = STATE / "server.log"
TIKTOKEN_CACHE = STATE / "tiktoken_cache"
TIKTOKEN_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"

# Mirrors run.py: psycopg's async pool cannot run on the Windows Proactor loop.
LAUNCHER = (
    "import asyncio, sys, uvicorn;"
    "sys.platform == 'win32' and asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy());"
    f"uvicorn.run('app.main:app', host='127.0.0.1', port={PORT}, loop='asyncio')"
)


def call(method: str, path: str, *, json_body=None, form=None, token: str | None = None, timeout: int = 60):
    """Send a request and return (status, parsed_body)."""
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except urllib.error.URLError as e:
        return 0, str(e)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode(errors="replace")


def seed_tiktoken_cache() -> None:
    """app/utils/graph.py downloads a tiktoken file at import; Python's TLS fails behind Avast, curl does not."""
    import hashlib

    target = TIKTOKEN_CACHE / hashlib.sha1(TIKTOKEN_URL.encode()).hexdigest()
    if target.exists() and target.stat().st_size > 0:
        return
    TIKTOKEN_CACHE.mkdir(parents=True, exist_ok=True)
    subprocess.run(["curl", "-sS", "-m", "90", "-o", str(target), TIKTOKEN_URL], check=True)


def build_ca_shim() -> Path:
    """Write a CA bundle (certifi + Windows root store) and a sitecustomize that points certifi.where() at it.

    Needed because Avast re-signs HTTPS traffic with a root only the Windows store trusts, and
    langchain_openai hardcodes certifi.where(), so SSL_CERT_FILE alone does not help.
    """
    import base64
    import ssl

    import certifi

    shim = STATE / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    bundle = shim / "ca-bundle.pem"
    parts = [Path(certifi.where()).read_text()]
    for store in ("ROOT", "CA"):
        for der, encoding, _trust in ssl.enum_certificates(store):
            if encoding == "x509_asn":
                body = base64.encodebytes(der).decode()
                parts.append(f"-----BEGIN CERTIFICATE-----{NL}{body}-----END CERTIFICATE-----{NL}")
    bundle.write_text(NL.join(parts))
    (shim / "sitecustomize.py").write_text(f"import certifi{NL}certifi.where = lambda: {str(bundle)!r}{NL}")
    return shim


def up() -> int:
    if call("GET", "/health", timeout=3)[0] == 200:
        print(f"already up on {BASE}")
        return 0
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / ".gitignore").write_text("*" + NL)
    seed_tiktoken_cache()
    env = {k: v for k, v in os.environ.items() if k != "SSLKEYLOGFILE"}  # Avast value crashes ssl with OPENSSL_Uplink
    env["TIKTOKEN_CACHE_DIR"] = str(TIKTOKEN_CACHE)
    if sys.platform == "win32":
        env["PYTHONPATH"] = os.pathsep.join([str(build_ca_shim()), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(LOG_FILE, "wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-c", LAUNCHER], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )
    PID_FILE.write_text(str(proc.pid))
    for _ in range(40):
        time.sleep(3)
        if proc.poll() is not None:
            print(f"server exited with {proc.returncode}; see {LOG_FILE}")
            return 1
        status, body = call("GET", "/health", timeout=3)
        if status == 200:
            print(f"up pid={proc.pid} {body}")
            return 0
    print(f"timeout waiting for /health; see {LOG_FILE}")
    return 1


def down() -> int:
    if not PID_FILE.exists():
        print("no pid file")
        return 0
    pid = PID_FILE.read_text().strip()
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], check=False)
    else:
        subprocess.run(["kill", pid], check=False)
    PID_FILE.unlink()
    return 0


def smoke() -> int:
    failures = 0

    def step(name: str, ok: bool, detail) -> None:
        nonlocal failures
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {str(detail)[:300]}")

    status, body = call("GET", "/health")
    step("health", status == 200 and body.get("components", {}).get("database") == "healthy", body)

    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    password = "Sm0ke-Test-Pass!"  # pragma: allowlist secret (throwaway smoke-test user)
    status, body = call("POST", "/api/v1/auth/register", json_body={"email": email, "password": password})
    step("register", status == 200 and "token" in body, body)
    user_token = body.get("token", {}).get("access_token") if isinstance(body, dict) else None

    status, body = call("POST", "/api/v1/auth/login", form={"email": email, "password": password, "grant_type": "password"})
    step("login", status == 200 and "access_token" in body, body)
    user_token = body.get("access_token", user_token) if isinstance(body, dict) else user_token

    status, body = call("POST", "/api/v1/auth/session", token=user_token)
    step("create_session", status == 200 and "session_id" in body, body)
    session_token = body.get("token", {}).get("access_token") if isinstance(body, dict) else None

    status, body = call("GET", "/api/v1/auth/sessions", token=user_token)
    step("list_sessions", status == 200 and isinstance(body, list) and len(body) >= 1, body)

    status, body = call("POST", "/api/v1/chatbot/chat", json_body={"messages": [{"role": "user", "content": "Say hi in 3 words."}]}, token=session_token, timeout=120)
    step("chat (needs a working LLM key)", status == 200, body)

    status, body = call("GET", "/api/v1/chatbot/messages", token=session_token)
    step("messages", status == 200, body)

    status, body = call("GET", "/api/v1/chatbot/messages")
    step("messages_without_token_rejected", status in (401, 403), status)
    return 1 if failures else 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252; LLM replies contain emoji
    cmd = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    if cmd == "up":
        return up()
    if cmd == "down":
        return down()
    if cmd == "status":
        print(call("GET", "/health"))
        return 0
    if cmd == "smoke":
        return smoke()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
