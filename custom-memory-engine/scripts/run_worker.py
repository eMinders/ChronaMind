"""Run the slow lane: claim a queued extraction, call the model outside the transaction, write the claims (``E-047``).

One loop, no broker. The queue is a table, the lease is a column, and the ordering guarantees come from
``FOR UPDATE SKIP LOCKED`` — so two workers on one database need no coordination, and a worker that dies mid-call loses
nothing but its lease (:func:`~custom_memory_engine.semantic.slow_lane.recover_stale` returns the job).

**The model is called with no transaction open**, which is the entire reason this path exists separately from
``process_one``: a five-second extraction inside a transaction holds locks for five seconds and bills the model again
when the transaction is retried.

Environment:

``CME_DATABASE_URL``          owner-level DSN, as for the host (each job runs under ``SET LOCAL ROLE``)
``CME_RUNTIME_ROLE``          the restricted role
``CME_SCHEMA``                managed schema (default ``cme_engine``)
``CME_WORKER_USERS``          comma-separated principals this worker serves (the queue is tenant-scoped)
``CME_LLM_BASE_URL``/``_KEY`` provider credentials
``CME_EXTRACTOR_MODEL``       the model used for extraction (``V2-D94``'s selection)
``CME_EMBEDDING_ASSET``/``_DIR``  the local embedder for the dense arm, or unset for lexical-only
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import structlog
from psycopg import AsyncConnection

from custom_memory_engine.engine.index_maintenance import JOB_KIND, perform_inline
from custom_memory_engine.engine.ingest import TurnInput
from custom_memory_engine.integrations.onnx_embedder import OnnxEmbedder, assets_from_environment
from custom_memory_engine.integrations.openai_compatible import OpenAiCompatibleAdjudicator, OpenAiCompatibleExtractor
from custom_memory_engine.integrations.provider import client_for
from custom_memory_engine.queue.contracts import Job
from custom_memory_engine.semantic.contracts import ExtractionRequest
from custom_memory_engine.semantic.ingestor import apply_extraction
from custom_memory_engine.semantic.slow_lane import claim_one, complete_one, recover_stale

logger = structlog.get_logger(__name__)
STOP = asyncio.Event()


async def load_turn(connection: AsyncConnection, user_id: str, turn_id: str, schema: str) -> TurnInput:
    """The verbatim turn a job refers to, read under the tenant's own row-level security."""
    from psycopg import sql
    from psycopg.rows import tuple_row

    async with connection.transaction():
        await connection.execute("SELECT pg_catalog.set_config('app.user_id', %s, true)", (user_id,))
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT id, user_id, session_id, sequence_no, role, content, occurred_at "
                    "FROM {t} WHERE id = %s AND user_id = %s"
                ).format(t=sql.Identifier(schema, "turn")),
                (turn_id, user_id),
            )
            row = await cursor.fetchone()
    if row is None:
        raise LookupError("turn_missing")  # the job is failed and eventually dead-lettered, which is visible
    return TurnInput(
        turn_id=str(row[0]),
        user_id=str(row[1]),
        session_id=str(row[2]),
        sequence_no=int(row[3]),
        role="assistant" if str(row[4]) == "assistant" else "user",  # the column is constrained to these two
        content=str(row[5]),
        occurred_at=row[6],
    )


#: The job kinds this worker serves. `index_rebuild` is enqueued by the `rebuild_index` operation (`A-014`,
#: `engine/index_maintenance.py`); a worker claiming only extraction would leave every maintenance job queued for ever.
KINDS = ("semantic_extraction", JOB_KIND)


async def run_once(connection: AsyncConnection, user_id: str) -> str:
    """One claim and the work it names, in whichever shape that work needs. Returns what happened, for a test."""
    settings = _settings()
    job = await claim_one(connection, user_id, schema_name=settings.schema, kinds=KINDS)
    if job is None:
        return "idle"
    if job.kind == JOB_KIND:
        return await _rebuild(connection, user_id, job, settings)
    return await _extract(connection, user_id, job, settings)


async def _rebuild(connection: AsyncConnection, user_id: str, job: Job, settings: Settings) -> str:
    """`rebuild_index`: two shapes that cannot share a transaction boundary.

    `fts`/`vector` must run ``REINDEX ... CONCURRENTLY``, which PostgreSQL refuses inside a transaction block, so it is
    performed on the worker's **own autocommit connection** with no transaction open, and the job is then completed by
    a writer that has nothing left to write. `closure_hash` is the opposite: it recomputes a derived column row by
    row, so it runs inside the job's transaction like any other database work.
    """
    from custom_memory_engine.engine.index_maintenance import logical_index, reindex_concurrently

    payload = job.payload if isinstance(job.payload, dict) else {}
    index = logical_index(payload.get("index"))
    if index == "closure_hash":

        async def reseal(conn: AsyncConnection, claimed: object, tick: object) -> None:
            del claimed, tick
            await perform_inline(conn, job, schema_name=settings.schema)

        result = await complete_one(connection, user_id, job, reseal, schema_name=settings.schema)
        return f"{result.status}:{job.id}"
    outcome = await reindex_concurrently(connection, index, schema_name=settings.schema)
    logger.info(
        "index_rebuild_completed",
        index=index,
        rebuilt=len(outcome.rebuilt),
        absent=len(outcome.absent),
    )

    async def nothing(conn: AsyncConnection, claimed: object, tick: object) -> None:
        del conn, claimed, tick

    result = await complete_one(connection, user_id, job, nothing, schema_name=settings.schema)
    return f"{result.status}:{job.id}"


async def _extract(connection: AsyncConnection, user_id: str, job: Job, settings: Settings) -> str:
    """`semantic_extraction`: claim, model outside any transaction, write inside one."""
    assert job.turn_id is not None  # a `semantic_extraction` job always names its turn (queue contract)
    turn = await load_turn(connection, user_id, job.turn_id, settings.schema)

    # --- outside any transaction: the model ----------------------------------------------------------------
    extractor = OpenAiCompatibleExtractor(settings.extractor)
    extraction = await extractor.extract(
        ExtractionRequest(text=turn.content, occurred_at=turn.occurred_at.isoformat(), scope_path=f"/u/{user_id}/")
    )

    # --- one transaction: the writes -----------------------------------------------------------------------
    async def work(conn: AsyncConnection, claimed: object, tick: object) -> None:
        del claimed
        await apply_extraction(
            conn,
            user_id=user_id,
            scope_path=f"/u/{user_id}/",
            turn=turn,
            episode_id=None,  # the fast lane already wrote it; `apply_extraction` does not reopen it
            extraction=extraction,
            embedder=settings.embedder,
            adjudicator=settings.adjudicator,
            schema_name=settings.schema,
            clock_tick=tick,  # type: ignore[arg-type]
        )

    result = await complete_one(connection, user_id, job, work, schema_name=settings.schema)
    return f"{result.status}:{job.id}"


class Settings:
    """Everything read once from the environment. No provider name and no model id is compiled in."""

    def __init__(self) -> None:
        self.dsn = _required("CME_DATABASE_URL")
        self.role = _required("CME_RUNTIME_ROLE")
        self.schema = os.environ.get("CME_SCHEMA", "cme_engine")
        assets = assets_from_environment(dict(os.environ))
        self.embedder = OnnxEmbedder(assets) if assets is not None else None
        protocol = os.environ.get("CME_LLM_PROTOCOL") or None
        self.extractor = client_for(
            base_url=_required("CME_LLM_BASE_URL"),
            api_key=_required("CME_LLM_API_KEY"),
            model=os.environ.get("CME_EXTRACTOR_MODEL", ""),
            protocol=protocol,
        )
        self.adjudicator = OpenAiCompatibleAdjudicator(
            client_for(
                base_url=_required("CME_LLM_BASE_URL"),
                api_key=_required("CME_LLM_API_KEY"),
                model=os.environ.get("CME_ADJUDICATOR_MODEL", "") or os.environ.get("CME_EXTRACTOR_MODEL", ""),
                protocol=protocol,
            )
        )


_SETTINGS: Settings | None = None


def _settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings()
    return _SETTINGS


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


async def main(users: list[str], *, once: bool = False) -> int:
    structlog.configure(processors=[structlog.processors.add_log_level, structlog.processors.JSONRenderer()])
    settings = _settings()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, STOP.set)
    async with await AsyncConnection.connect(settings.dsn, autocommit=True, connect_timeout=5) as connection:
        for user in users:
            recovered = await recover_stale(connection, user, schema_name=settings.schema)
            if recovered:
                logger.warning("slow_lane_recovered_on_start", user_id=user, recovered=recovered)
        while not STOP.is_set():
            worked = 0
            for user in users:
                try:
                    outcome = await run_once(connection, user)
                except Exception as error:  # noqa: BLE001 - one bad job must not stop the worker
                    logger.exception("slow_lane_tick_failed", user_id=user, error_type=type(error).__name__)
                    continue
                worked += outcome != "idle"
            if once:
                return 0
            if not worked:
                # idle: nothing is due. A short sleep is cheaper than a LISTEN/NOTIFY round trip and is enough for the
                # latency budget of an asynchronous lane (seconds, not milliseconds).
                try:
                    await asyncio.wait_for(STOP.wait(), timeout=5.0)
                except TimeoutError:
                    pass
    logger.info("slow_lane_stopped")
    return 0


if __name__ == "__main__":
    principals = [item.strip() for item in os.environ.get("CME_WORKER_USERS", "").split(",") if item.strip()]
    if not principals:
        print("CME_WORKER_USERS is required", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(principals, once="--once" in sys.argv)))
