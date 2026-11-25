"""CLI entry point for periodic per-tenant maintenance (``#255``, ``A-014``, ``V2-D119``).

Thin process wrapper around :mod:`custom_memory_engine.worker.maintenance`: parses flags, reads ``CME_*``
environment variables, opens an autocommit database connection, handles ``SIGINT``/``SIGTERM``, and exits cleanly
(``0`` success, ``1`` tenant/runtime error, ``2`` invalid/missing configuration).

    python scripts/run_maintenance.py --once
    python scripts/run_maintenance.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

import structlog
from psycopg import AsyncConnection

from custom_memory_engine.worker.maintenance import serve_maintenance, settings_from_environment


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run periodic maintenance across configured CME tenants.")
    parser.add_argument("--once", action="store_true", help="Run one maintenance pass and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect candidate counts without mutating data.")
    return parser.parse_args(argv)


async def main(*, once: bool = False, dry_run: bool = False) -> int:
    """Configure logging, validate settings, connect to PostgreSQL, and execute maintenance."""
    structlog.configure(processors=[structlog.processors.add_log_level, structlog.processors.JSONRenderer()])
    try:
        settings = settings_from_environment(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)  # static code naming only the variable, never its value
        return 2
    dsn = os.environ.get("CME_DATABASE_URL", "").strip()
    if not dsn:
        print("cme_database_url_required", file=sys.stderr)
        return 2
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=5) as connection:
            return await serve_maintenance(connection, settings, stop, once=once, dry_run=dry_run)
    except Exception as error:  # noqa: BLE001 - exit non-zero cleanly without dumping DSN/credentials
        structlog.get_logger(__name__).error("maintenance_process_failed", error_type=type(error).__name__)
        return 1


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(asyncio.run(main(once=args.once, dry_run=args.dry_run)))
