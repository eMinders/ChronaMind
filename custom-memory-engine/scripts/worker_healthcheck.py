"""CLI for the worker's queue probe (``V2-D103``). The rule lives in `queue.health`; this is the entry point.

Deployment code, deliberately thin: it reads the environment, connects, calls :func:`queue_health`, prints one
content-free line, and exits with the verdict's code (0 healthy, 1 degraded, 2 unhealthy). It never imports the
worker's model wiring — a probe must not load a 2.2 GB ONNX graph to answer "is the loop alive".

    python scripts/worker_healthcheck.py

`deploy/README.md` documents the three places it runs: a container `HEALTHCHECK`, a systemd `ExecStartPost`, and an
operator's shell.
"""

from __future__ import annotations

import asyncio
import os
import sys

from psycopg import AsyncConnection


async def probe(dsn: str, schema: str, *, users: tuple[str, ...]) -> int:
    from custom_memory_engine.queue.health import queue_health

    async with await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=5) as connection:
        health = await queue_health(connection, users=users, schema_name=schema)
    print(health.describe(), file=sys.stderr)
    return health.exit_code


def main() -> int:
    dsn = os.environ.get("CME_DATABASE_URL", "").strip()
    schema = os.environ.get("CME_SCHEMA", "cme_engine")
    users = tuple(item.strip() for item in os.environ.get("CME_WORKER_USERS", "").split(",") if item.strip())
    if not dsn or not users:
        print("CME_DATABASE_URL and CME_WORKER_USERS are required", file=sys.stderr)
        return 2
    try:
        return asyncio.run(probe(dsn, schema, users=users))
    except Exception as error:  # noqa: BLE001 - a probe's only output is an exit code
        print(f"unhealthy: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
