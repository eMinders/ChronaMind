"""End-to-end smoke check for the ChronaMind live demo stack.

Registers a fresh user, creates a chat session, sends "I live in Berlin" then
"I moved to Paris", and asserts that an engine ``query`` as that user returns
exactly one non-episode fact containing "Paris" within 20 seconds.

Usage (from the repository root):
    uv run python scripts/demo/check.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

os.environ.pop("SSLKEYLOGFILE", None)

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env.demo"
APP_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_POLL_INTERVAL = 0.5
CHAT_TIMEOUT_SECONDS = 90.0

type RequestFn = Callable[..., tuple[int, object]]


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


def request_json(
    method: str,
    url: str,
    *,
    json_body: dict[str, object] | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, object]:
    """Send an HTTP JSON request and return ``(status_code, parsed_body)``."""
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    headers: dict[str, str] = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _loopback_opener().open(request, timeout=timeout) as response:
            status_code, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status_code, raw = error.code, error.read()
    except urllib.error.URLError, TimeoutError, OSError:
        return 0, "connection_error"
    try:
        return status_code, json.loads(raw)
    except ValueError:
        return status_code, raw.decode("utf-8", errors="replace")


def _non_episode_facts(result: object) -> list[str]:
    """Extract non-episode fact contents from an engine ``query`` result object."""
    if not isinstance(result, dict):
        return []
    facts = result.get("facts")
    if not isinstance(facts, list):
        return []
    return [
        str(item.get("content") or "") for item in facts if isinstance(item, dict) and item.get("kind") != "episode"
    ]


def _query_facts(
    engine_url: str,
    engine_token: str,
    user_id: str,
    request_fn: RequestFn,
) -> tuple[int, list[str]]:
    """Run ``query`` (``current_state``) as ``user_id`` and return ``(status, non_episode_facts)``."""
    status, body = request_fn(
        "POST",
        f"{engine_url.rstrip('/')}/v1/call",
        json_body={
            "operation": "query",
            "principal": user_id,
            "arguments": {"request": {"mode": "current_state", "scope": f"/u/{user_id}/", "limit": 200}},
        },
        token=engine_token,
        timeout=15.0,
    )
    if status != 200 or not isinstance(body, dict):
        return status, []
    return status, _non_episode_facts(body.get("result"))


def _poll_facts(
    engine_url: str,
    engine_token: str,
    user_id: str,
    predicate: Callable[[list[str]], bool],
    *,
    request_fn: RequestFn,
    timeout_seconds: float,
    poll_interval: float,
) -> tuple[bool, list[str]]:
    """Poll engine ``query`` until ``predicate(facts)`` holds or ``timeout_seconds`` elapses."""
    deadline = time.monotonic() + timeout_seconds
    last_facts: list[str] = []
    while True:
        status, facts = _query_facts(engine_url, engine_token, user_id, request_fn)
        if status == 200:
            last_facts = facts
            if predicate(facts):
                return True, facts
        if time.monotonic() >= deadline:
            return False, last_facts
        if poll_interval > 0:
            time.sleep(poll_interval)


def _extract_token(body: object) -> str | None:
    """Return ``body['token']['access_token']`` if present and a non-empty string."""
    if not isinstance(body, dict):
        return None
    token_obj = body.get("token")
    if not isinstance(token_obj, dict):
        return None
    access_token = token_obj.get("access_token")
    return access_token if isinstance(access_token, str) and access_token else None


def run_check(
    *,
    app_url: str = APP_URL,
    engine_url: str,
    engine_token: str,
    request_fn: RequestFn = request_json,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> bool:
    """Execute the end-to-end Berlin -> Paris memory supersession check."""
    base = app_url.rstrip("/")
    email = f"demo-check-{uuid.uuid4().hex[:10]}@example.com"
    password = f"Demo-{secrets.token_hex(6)}!A1"

    status, register_body = request_fn(
        "POST",
        f"{base}/api/v1/auth/register",
        json_body={"email": email, "password": password},
        timeout=30.0,
    )
    user_token = _extract_token(register_body)
    user_id = str(register_body["id"]) if isinstance(register_body, dict) and "id" in register_body else None
    if status != 200 or not user_token or not user_id:
        print(f"FAIL: register failed (status {status})", file=sys.stderr)
        return False

    status, session_body = request_fn(
        "POST",
        f"{base}/api/v1/auth/session",
        token=user_token,
        timeout=30.0,
    )
    session_token = _extract_token(session_body)
    if status != 200 or not session_token:
        print(f"FAIL: create session failed (status {status})", file=sys.stderr)
        return False

    status, _ = request_fn(
        "POST",
        f"{base}/api/v1/chatbot/chat",
        json_body={"messages": [{"role": "user", "content": "I live in Berlin"}]},
        token=session_token,
        timeout=CHAT_TIMEOUT_SECONDS,
    )
    if status != 200:
        print(f"FAIL: chat turn 1 ('I live in Berlin') failed (status {status})", file=sys.stderr)
        return False

    # Wait for turn 1's background memory write to land before sending turn 2, avoiding an async write race.
    berlin_landed, berlin_facts = _poll_facts(
        engine_url,
        engine_token,
        user_id,
        lambda facts: any("Berlin" in fact for fact in facts),
        request_fn=request_fn,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )
    if not berlin_landed:
        print(
            f"FAIL: Berlin fact did not appear within {timeout_seconds:.0f}s (observed {len(berlin_facts)} fact(s))",
            file=sys.stderr,
        )
        return False

    status, _ = request_fn(
        "POST",
        f"{base}/api/v1/chatbot/chat",
        json_body={"messages": [{"role": "user", "content": "I moved to Paris"}]},
        token=session_token,
        timeout=CHAT_TIMEOUT_SECONDS,
    )
    if status != 200:
        print(f"FAIL: chat turn 2 ('I moved to Paris') failed (status {status})", file=sys.stderr)
        return False

    paris_ok, paris_facts = _poll_facts(
        engine_url,
        engine_token,
        user_id,
        lambda facts: len(facts) == 1 and "Paris" in facts[0],
        request_fn=request_fn,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )
    if not paris_ok:
        print(
            f"FAIL: expected exactly 1 non-episode fact containing 'Paris' within {timeout_seconds:.0f}s; "
            f"observed {len(paris_facts)} fact(s): {paris_facts}",
            file=sys.stderr,
        )
        return False

    print(f"PASS: user {user_id} has 1 non-episode fact: {paris_facts[0]!r}")
    return True


def main() -> int:
    """CLI entrypoint for checking the live demo stack."""
    if not ENV_FILE.exists():
        print("FAIL: demo stack is not configured. Run `uv run python scripts/demo/up.py` first.", file=sys.stderr)
        return 1
    values = parse_env_file(ENV_FILE)
    engine_url = values.get("MEMORY_ENGINE_URL", "")
    # Use the Tier B agent token only; Tier C (`MEMORY_ENGINE_USER_TOKEN`) is reserved for `/api/v1/memory/*`.
    engine_token = values.get("MEMORY_ENGINE_TOKEN", "")
    if not engine_url or not engine_token:
        print("FAIL: missing engine settings in scripts/demo/.env.demo. Re-run `up.py`.", file=sys.stderr)
        return 1
    ok = run_check(app_url=APP_URL, engine_url=engine_url, engine_token=engine_token)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
