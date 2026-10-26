"""Private bounded JSON-line protocol for isolated, pinned vendor processes."""

import json
import sys
import threading
from typing import Any

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
_OUTPUT = sys.__stdout__
_LOCK = threading.Lock()


def send(value: dict[str, Any]) -> None:
    if _OUTPUT is None:
        raise RuntimeError("worker_output_pipe_required")
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("worker_message_limit")
    _OUTPUT.write(raw + "\n")
    _OUTPUT.flush()


def receive() -> dict[str, Any]:
    raw = sys.stdin.readline(MAX_MESSAGE_BYTES + 2)
    if not raw or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith("\n"):
        raise ValueError("worker_message_limit_or_eof")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("worker_object_required")
    return value


def model_call(kind: str, **payload: Any) -> Any:
    # Graphiti can fan out async work. Synchronous bridge I/O deliberately
    # serializes these requests so all tasks settle inside one metered operation.
    with _LOCK:
        send({"type": kind, **payload})
        response = receive()
        if response.get("ok") is not True:
            raise RuntimeError("parent_model_call_failed")
        return response["result"]
