"""Bounded recovery for exact parent-owned Falkor graph names after worker exit.

This private helper imports Redis only. It never constructs Graphiti, provider
clients, or model services. Its machine protocol emits static status, never input.
"""

import json
import re
import sys
from typing import Any

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

MAX_INPUT_BYTES = 8 * 1024 * 1024


def cleanup_owned_graphs(graph: dict[str, Any], tenants: list[str]) -> None:
    """Validate the complete trusted-parent scope before making any mutation."""
    if (
        not isinstance(graph, dict)
        or set(graph) != {"host", "port"}
        or not isinstance(graph.get("host"), str)
        or graph.get("host") not in {"127.0.0.1", "localhost"}
        or type(graph.get("port")) is not int
        or not 1 <= graph["port"] <= 65535
        or not isinstance(tenants, list)
        or any(not isinstance(name, str) or not re.fullmatch(r"cme_[a-f0-9]{64}", name) for name in tenants)
    ):
        raise ValueError("invalid_owned_graph_cleanup_scope")
    if not tenants:
        return
    # Canonical loopback avoids hostname expansion or external fallback. Never
    # accept credentials, URLs, Redis DB indexes or arbitrary command parameters.
    with redis.Redis(
        host="127.0.0.1",
        port=graph["port"],
        socket_timeout=3,
        socket_connect_timeout=3,
        retry=Retry(NoBackoff(), 0),
        retry_on_error=[],
        health_check_interval=0,
    ) as connection:
        for tenant in dict.fromkeys(tenants):
            if connection.exists(tenant):
                connection.execute_command("GRAPH.DELETE", tenant)


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("cleanup_input_limit")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"graph", "tenants"}:
            raise ValueError("cleanup_request_required")
        cleanup_owned_graphs(value["graph"], value["tenants"])
    except Exception:
        sys.stdout.write('{"ok":false,"error":"owned_graph_cleanup_failed"}\n')
        raise SystemExit(1) from None
    sys.stdout.write('{"ok":true}\n')


if __name__ == "__main__":
    main()
