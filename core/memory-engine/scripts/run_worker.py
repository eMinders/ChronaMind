"""Run the slow-lane worker: the process around :func:`custom_memory_engine.worker.serve` (``E-047``, ``V2-D106``).

The loop, the model calls and the writes are engine code in ``custom_memory_engine.worker``, where they are tested.
What is here is only what a process needs: read the environment, connect, turn ``SIGTERM``/``SIGINT`` into the stop
event, and exit with the loop's status. A stop finishes the job in hand and returns; it never abandons a held lease,
and if the process is killed instead, the next live worker returns the lease within one idle cycle.

Environment (see ``deploy/README.md`` for the full table):

``CME_DATABASE_URL``              owner-level DSN (``REINDEX`` needs the owner; every tenant write runs under the role)
``CME_RUNTIME_ROLE``              the restricted role each tenant transaction runs as
``CME_WORKER_USERS``              comma-separated principals this worker serves
``CME_LLM_BASE_URL``/``_API_KEY`` provider credentials, from the env file only
``CME_EXTRACTOR_MODEL``           extraction model; ``CME_ADJUDICATOR_MODEL`` for both adjudicators (default: same)
``CME_EMBEDDING_ASSET``/``_DIR``  the local embedder, or unset for lexical-only

    python scripts/run_worker.py           # serve until SIGTERM
    python scripts/run_worker.py --once    # one pass over every user, then exit
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import structlog
from psycopg import AsyncConnection

from custom_memory_engine.worker import serve, settings_from_environment


async def main(*, once: bool = False) -> int:
    structlog.configure(processors=[structlog.processors.add_log_level, structlog.processors.JSONRenderer()])
    try:
        settings = settings_from_environment(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)  # a static code naming the variable, never its value
        return 2
    dsn = os.environ.get("CME_DATABASE_URL", "").strip()
    if not dsn:
        print("cme_database_url_required", file=sys.stderr)
        return 2
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop.set)
    async with await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=5) as connection:
        return await serve(connection, settings, stop, once=once)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(once="--once" in sys.argv)))
