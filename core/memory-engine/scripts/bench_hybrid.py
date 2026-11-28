"""Measured latency of the **hybrid** read path (W3.5, ``E-043``): the honest re-baseline.

Gate G3 records "Retrieval P95 at v1 scale within 2× of the 27 ms reference — met, 11.82 ms" on the strength of
``retrieval_stages_2000.json``. That number measures the *recency scan* the V2 ``query`` path performed before
``V2-D86``: no vector arm, no lexical arm, no trigram arm and no ranking. It is honest about the code and useless about
the product, so it is re-baselined here against the path that answers a question.

Two configurations are measured and reported separately, because they are what a deployment actually runs:

* **lexical** -- ``text`` only: the ``'simple'`` full-text arm plus the ``pg_trgm`` arm, fused by RRF. This is the
  zero-model configuration, and it is what an install without an embedder runs.
* **hybrid** -- ``text`` **and** a 1024-wide vector: the dense arm joins the fuse over the multilingual ``halfvec``
  slot (migration 23). The vector is the seeded ``embedding()`` fixture, so the measurement is about the query path and
  not about embedding quality.

Every number is measured by this command into a disposable schema; nothing is estimated. The five ``E-036`` stages are
recorded beside the total so the cost attribution does not move.

    uv run --env-file .env python scripts/bench_hybrid.py --rows 2000 --iterations 50
    # and, for the report file:
    uv run --env-file .env python scripts/bench_hybrid.py --report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import structlog
from psycopg import AsyncConnection, sql

from custom_memory_engine.api.entry import CallerContext, Credential
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.config import TestDatabaseConfig
from custom_memory_engine.engine.planning import STAGES
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_retrieval import SCHEMA_SQL, seed, summarize  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[1] / "reports/benchmarks/retrieval_hybrid_2000.json"
#: The multilingual embedder whose slot the dense arm searches. Distinct from ``bench_retrieval.MODEL`` (the 384-wide
#: v1 baseline) because a row fills exactly one embedding slot -- the coexistence a re-embedding rollout needs.
MULTILINGUAL_MODEL = "BAAI/bge-m3"
#: The query the arms rank. It shares one token with the seeded vocabulary ("morning"), which is what a real question
#: looks like against a synthetic corpus: the point is to measure the arms, not to fake a hit rate.
QUERY_TEXT = "morning coffee guitar"


async def measure(engine, ctx: CallerContext, request: dict[str, object], iterations: int) -> dict[str, object]:
    """Time ``query`` for one request shape, plus each ``E-036`` stage, and return the summary."""
    totals: list[float] = []
    stages: dict[str, list[float]] = {stage: [] for stage in STAGES}
    rendered = 0
    for _ in range(iterations):
        result = await engine.call(ctx, "query", {"request": request})
        assert isinstance(result, QueryResult)
        totals.append(result.stage_costs.total_latency_ms if result.stage_costs is not None else 0.0)
        if result.stage_costs is not None:
            for entry in result.stage_costs.stages:
                stages[entry.stage].append(entry.latency_ms)
        rendered = len(result.facts) + len(result.hypotheses) + len(result.procedures) + len(result.conflicts)
    return {
        "total": summarize(totals),
        "stages": {stage: summarize(samples) if samples else {} for stage, samples in stages.items()},
        "items_rendered": rendered,
    }


async def main() -> int:
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=2000, help="beliefs per user")
    parser.add_argument("--users", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--report", action="store_true", help="write the JSON report into reports/benchmarks/")
    args = parser.parse_args()

    config = TestDatabaseConfig.from_environment(os.environ)
    schema = "cme_hyb_" + uuid4().hex[:12]
    role = "cme_hyb_rt_" + uuid4().hex[:8]
    connection = await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    )
    try:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await connection.execute(
            cast(
                LiteralString,
                "\n".join(
                    SCHEMA_SQL.with_name(name).read_text(encoding="utf-8")
                    for _, name in __import__(
                        "custom_memory_engine.schema.migrate", fromlist=["MIGRATIONS"]
                    ).MIGRATIONS
                ),
            ),
            prepare=False,
        )
        await seed(connection, args.rows, args.users, typed=True)
        # The seed writes no vectors, so the dense arm would have nothing to search. Attach one per version in the
        # multilingual slot, which is the configuration migration 23 exists for.
        await connection.execute(
            "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding_1024) "
            "SELECT m.id, %s, m.user_id, %s::halfvec(1024) FROM memory_version AS m",
            (MULTILINGUAL_MODEL, _literal(embed_1024_vector())),
        )
        await connection.execute("ANALYZE")

        await provision_runtime_role(connection, schema, role)
        runtime = Runtime(connection=connection, role=role, schema_name=schema)
        user = "bench-0"
        scope = f"/u/{user}/"
        ctx = CallerContext.from_credential(
            Credential(user, "C", False),
            write_scope=scope,
            read_set=resolve_read_set(user, [Selector.of(scope)], []),
        )
        engine = build_engine(runtime)
        register_read_handlers(engine, runtime, collect_cost=True)

        lexical = await measure(engine, ctx, {"text": QUERY_TEXT, "limit": args.limit}, args.iterations)
        hybrid = await measure(
            engine,
            ctx,
            {
                "text": QUERY_TEXT,
                "limit": args.limit,
                "embedding": list(embed_1024_vector()),
                "embedding_model": MULTILINGUAL_MODEL,
            },
            args.iterations,
        )
        recency = await measure(engine, ctx, {"limit": args.limit}, args.iterations)
    finally:
        try:
            await connection.execute(sql.SQL("RESET ROLE"))
            await connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        except Exception:  # the role may not exist if provisioning failed; the schema drop is what matters
            pass
        await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        await connection.close()

    report = {
        "schema": "hybrid retrieval, measured through Engine.call (W3.5, E-043, V2-D86)",
        "generated_by": "scripts/bench_hybrid.py",
        "rows_per_user": args.rows,
        "users": args.users,
        "limit": args.limit,
        "iterations": args.iterations,
        "embedding_model": MULTILINGUAL_MODEL,
        "query_text": QUERY_TEXT,
        "arms": {
            "lexical": "to_tsvector('simple') + pg_trgm, fused by RRF",
            "hybrid": "lexical + dense over the halfvec(1024) slot, fused by RRF",
            "recency": "no text: the pre-W3.5 candidate generation, for comparison",
        },
        "lexical_only": lexical,
        "hybrid": hybrid,
        "recency_scan": recency,
        "notes": [
            "P95 is the 95th percentile of the per-call total the engine itself records (E-036 stage costs).",
            "No generative model runs on this path; the dense arm feeds a caller-supplied vector.",
            "The dense arm is HNSW-indexed only in the multilingual slots (migration 23); the legacy 384 slot",
            "stays an exact scan on purpose, so the frozen v1 numbers keep their meaning.",
            "At 2000 rows in one scope PostgreSQL may choose a scan over the HNSW index; the number measures what a",
            "deployment of this size actually runs, not what the index could do on a larger corpus.",
        ],
    }
    print(json.dumps(report, indent=2))
    if args.report:
        OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUTPUT}", file=sys.stderr)
    return 0


def embed_1024_vector() -> tuple[float, ...]:
    """One deterministic 1024-wide unit vector: the shape the multilingual slot holds, with no model anywhere."""
    values = [0.0] * 1024
    values[0] = 1.0
    return tuple(values)


def _literal(values: tuple[float, ...]) -> str:
    """A pgvector literal. ``halfvec`` rejects PostgreSQL's ``{...}`` array form (see ``hybrid.vector_literal``)."""
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
