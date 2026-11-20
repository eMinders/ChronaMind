"""Measured retrieval latency for the read path (no fabricated numbers).

Seeds N single-valued beliefs per user into a disposable schema, then times the
dense, lexical and fused paths plus the planner's use of the new indexes.
With ``--report``, also times the V2 five-stage ``query`` path through ``Engine.call``
(per-stage P50/P95, measurement overhead, and G3 comparison against the frozen E-002
baseline in ``reports/benchmarks/retrieval_2000.json``).

    uv run --env-file .env python scripts/bench_retrieval.py --rows 2000 --iterations 50
    uv run --env-file .env python scripts/bench_retrieval.py --report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import statistics
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import LiteralString, cast
from uuid import uuid4

import structlog
from psycopg import AsyncConnection, sql

from custom_memory_engine.api.entry import CallerContext, Credential
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.config import TestDatabaseConfig
from custom_memory_engine.engine.cost import STAGE_UNITS, CostReport
from custom_memory_engine.engine.planning import STAGES
from custom_memory_engine.engine.predicates import current_state
from custom_memory_engine.engine.retrieval import rank_versions
from custom_memory_engine.schema import migrate
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "src/custom_memory_engine/schema/001_full_schema.sql"
BASELINE_PATH = Path(__file__).resolve().parents[1] / "reports/benchmarks/retrieval_2000.json"
MODEL = "BAAI/bge-small-en-v1.5"
WORDS = (
    "vegetarian fish morning run coffee tea guitar piano london berlin summer winter "
    "laptop python rust reading hiking cycling swimming painting chess photography"
).split()

# E-002 (#51, PLAN/V2/BASELINE.md §5): frozen cme-v1.0.0 comparison figures at 2000 candidates.
E002_BASELINE_FILE = "reports/benchmarks/retrieval_2000.json"
E002_BASELINE_TAG = "cme-v1.0.0"
E002_DENSE_P50_MS = 26.99
E002_FUSED_P95_MS = 29.84
E002_STRICTER_2X_LIMIT_MS = round(E002_DENSE_P50_MS * 2.0, 2)  # 53.98 ms
E002_FUSED_2X_LIMIT_MS = round(E002_FUSED_P95_MS * 2.0, 2)  # 59.68 ms


def embedding(rng: random.Random) -> list[float]:
    values = [rng.gauss(0.0, 1.0) for _ in range(384)]
    norm = sum(value * value for value in values) ** 0.5
    return [value / norm for value in values]


async def seed(conn: AsyncConnection, rows: int, users: int, *, typed: bool = False) -> datetime:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    # Distinct predicates per belief: the exclusion constraint is keyed on
    # (subject_key, predicate_key), so seeded rows must not share one.
    await conn.execute(
        "INSERT INTO predicate_schema SELECT 'fact-' || g, 'single', 'semantic' FROM generate_series(0, %s) AS g",
        (rows,),
    )
    rng = random.Random(20261026)
    for user_index in range(users):
        user = f"bench-{user_index}"
        scope_path = f"/u/{user}/"
        await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, user, start))
        await conn.execute("INSERT INTO session VALUES (%s, %s, %s, NULL)", (f"s-{user}", user, start))
        await conn.execute("SELECT set_config('app.user_id', %s, false)", (user,))
        async with conn.transaction():
            for index in range(rows):
                version_id = f"{user}-v{index}"
                content = " ".join(rng.sample(WORDS, 5)) + f" note {index}"
                await conn.execute(
                    "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                    "VALUES (%s, %s, %s, %s, 'user', %s, %s)",
                    (f"t-{version_id}", f"s-{user}", user, index + 1, content, start),
                )
                if typed:
                    await conn.execute(
                        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                        "single_valued, content, event_range, recorded_range, layer, kind, lifecycle, retention, "
                        "scope_path, visibility) VALUES "
                        "(%s, %s, %s, 'active', 'user', %s, true, %s, tstzrange(%s, NULL, '[)'), "
                        "tstzrange(clock_now(), NULL, '[)'), 'episodic', 'episode', 'observed', 'durable', "
                        "%s, 'private')",
                        (
                            version_id,
                            f"key-{index}",
                            user,
                            f"fact-{index}",
                            content,
                            start + timedelta(days=index % 365),
                            scope_path,
                        ),
                    )
                else:
                    await conn.execute(
                        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                        "single_valued, content, event_range, recorded_range) VALUES "
                        "(%s, %s, %s, 'active', 'user', %s, true, %s, tstzrange(%s, NULL, '[)'), "
                        "tstzrange(clock_now(), NULL, '[)'))",
                        (
                            version_id,
                            f"key-{index}",
                            user,
                            f"fact-{index}",
                            content,
                            start + timedelta(days=index % 365),
                        ),
                    )
                await conn.execute(
                    "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                    "VALUES (%s, %s, %s, %s, 'support')",
                    (version_id, f"t-{version_id}", user, content[:20]),
                )
                await conn.execute(
                    "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding) "
                    "VALUES (%s, %s, %s, %s)",
                    (version_id, MODEL, user, embedding(rng)),
                )
    return start + timedelta(days=365)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[position]


def summarize(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
    }


async def timed(coro_factory: Callable[[], Awaitable[object]], iterations: int) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = perf_counter()
        await coro_factory()
        samples.append((perf_counter() - started) * 1000.0)
    return summarize(samples)


async def benchmark_staged_report(
    connection: AsyncConnection,
    *,
    schema: str,
    iterations: int,
    limit: int,
) -> dict[str, object]:
    """Measure V2 five-stage ``query`` latency per stage, plus instrumentation overhead."""
    role = "cme_bench_rt_" + uuid4().hex[:12]
    await provision_runtime_role(connection, schema, role)
    try:
        runtime = Runtime(connection=connection, role=role, schema_name=schema)
        user = "bench-0"
        scope = f"/u/{user}/"
        ctx = CallerContext.from_credential(
            Credential(user, "C", False),
            write_scope=scope,
            read_set=resolve_read_set(user, [Selector.of(scope)], []),
        )
        request: dict[str, object] = {"request": {"scope": scope, "limit": limit}}

        instrumented_engine = build_engine(runtime)
        register_read_handlers(instrumented_engine, runtime, collect_cost=True)
        uninstrumented_engine = build_engine(runtime)
        register_read_handlers(uninstrumented_engine, runtime, collect_cost=False)

        # Warm past psycopg's default prepare_threshold (5) and refresh planner statistics so neither
        # instrumented nor uninstrumented samples absorb one-time statement preparation or cold table stats.
        await connection.execute("ANALYZE")
        for _ in range(6):
            warm = await instrumented_engine.call(ctx, "query", request)
            assert isinstance(warm, QueryResult) and warm.stage_costs is not None
            warm_plain = await uninstrumented_engine.call(ctx, "query", request)
            assert isinstance(warm_plain, QueryResult) and warm_plain.stage_costs is None

        # Fresh instrumented engine so the CostReport covers only the timed sample window.
        instrumented_engine = build_engine(runtime)
        register_read_handlers(instrumented_engine, runtime, collect_cost=True)

        stage_samples: dict[str, list[float]] = {stage: [] for stage in STAGES}
        total_instrumented: list[float] = []
        total_uninstrumented: list[float] = []
        last_candidates: dict[str, dict[str, int | str]] = {}

        # Interleave instrumented and uninstrumented calls to eliminate ordering/cache drift.
        for index in range(iterations):
            if index % 2 == 0:
                started = perf_counter()
                result = await instrumented_engine.call(ctx, "query", request)
                total_instrumented.append((perf_counter() - started) * 1000.0)
                started = perf_counter()
                plain = await uninstrumented_engine.call(ctx, "query", request)
                total_uninstrumented.append((perf_counter() - started) * 1000.0)
            else:
                started = perf_counter()
                plain = await uninstrumented_engine.call(ctx, "query", request)
                total_uninstrumented.append((perf_counter() - started) * 1000.0)
                started = perf_counter()
                result = await instrumented_engine.call(ctx, "query", request)
                total_instrumented.append((perf_counter() - started) * 1000.0)
            assert isinstance(result, QueryResult) and result.stage_costs is not None
            assert isinstance(plain, QueryResult) and plain.stage_costs is None
            for entry in result.stage_costs.stages:
                stage_samples[entry.stage].append(entry.latency_ms)
                last_candidates[entry.stage] = {
                    "unit": STAGE_UNITS[entry.stage],
                    "candidates_in": entry.candidates_in,
                    "candidates_out": entry.candidates_out,
                    "tokens_considered": entry.tokens_considered,
                    "tokens_rendered": entry.tokens_rendered,
                }

        cost_report = await instrumented_engine.call(ctx, "cost", {"window": None})
        assert isinstance(cost_report, CostReport)

        inst_summary = summarize(total_instrumented)
        uninst_summary = summarize(total_uninstrumented)
        p50_delta_ms = round(inst_summary["p50_ms"] - uninst_summary["p50_ms"], 3)
        p95_delta_ms = round(inst_summary["p95_ms"] - uninst_summary["p95_ms"], 3)
        p50_pct = round((p50_delta_ms / uninst_summary["p50_ms"]) * 100.0, 2) if uninst_summary["p50_ms"] > 0 else 0.0
        p95_pct = round((p95_delta_ms / uninst_summary["p95_ms"]) * 100.0, 2) if uninst_summary["p95_ms"] > 0 else 0.0

        return {
            "limit": limit,
            "iterations": iterations,
            "end_to_end": inst_summary,
            "stages": {
                stage: {
                    **summarize(stage_samples[stage]),
                    **last_candidates[stage],
                }
                for stage in STAGES
            },
            "overhead": {
                "instrumented_p50_ms": inst_summary["p50_ms"],
                "uninstrumented_p50_ms": uninst_summary["p50_ms"],
                "delta_p50_ms": p50_delta_ms,
                "delta_p50_pct": p50_pct,
                "instrumented_p95_ms": inst_summary["p95_ms"],
                "uninstrumented_p95_ms": uninst_summary["p95_ms"],
                "delta_p95_ms": p95_delta_ms,
                "delta_p95_pct": p95_pct,
            },
            "cost_operation_report": cost_report.to_dict(),
        }
    finally:
        await connection.execute("RESET ROLE")
        await connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        await connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def main() -> int:
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=2000, help="beliefs per user")
    parser.add_argument("--users", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--limit", type=int, default=50, help="row limit for V2 staged report")
    parser.add_argument(
        "--report",
        action="store_true",
        help="include per-stage V2 retrieval P50/P95, overhead quantification and E-002 G3 comparison",
    )
    parser.add_argument("--output", type=Path, default=None, help="optional path to write JSON report")
    args = parser.parse_args()

    config = TestDatabaseConfig.from_environment(os.environ)
    schema = "cme_bench_" + uuid4().hex
    connection = await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    )
    try:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await connection.execute(
            cast(
                LiteralString,
                "\n".join(SCHEMA_SQL.with_name(name).read_text(encoding="utf-8") for _, name in migrate.MIGRATIONS),
            ),
            prepare=False,
        )
        reference = await seed(connection, args.rows, args.users, typed=args.report)
        query_embedding = embedding(random.Random(7))
        await connection.execute("SELECT set_config('app.user_id', 'bench-0', false)")

        outcome = await rank_versions(
            connection,
            current_state("bench-0", reference),
            query_text="morning coffee guitar",
            query_embedding=query_embedding,
            fusion="rrf",
            schema_name=schema,
        )
        fused = await timed(
            lambda: rank_versions(
                connection,
                current_state("bench-0", reference),
                query_text="morning coffee",
                query_embedding=query_embedding,
                fusion="rrf",
                schema_name=schema,
            ),
            args.iterations,
        )
        results: dict[str, object] = {
            "rows_per_user": args.rows,
            "users": args.users,
            "candidates": outcome.candidates,
            "dense": await timed(
                lambda: rank_versions(
                    connection,
                    current_state("bench-0", reference),
                    query_text="morning",
                    query_embedding=query_embedding,
                    fusion="dense",
                    schema_name=schema,
                ),
                args.iterations,
            ),
            "lexical": await timed(
                lambda: rank_versions(
                    connection,
                    current_state("bench-0", reference),
                    query_text="morning coffee",
                    fusion="lexical",
                    schema_name=schema,
                ),
                args.iterations,
            ),
            "fused": fused,
        }

        cursor = await connection.execute(
            "EXPLAIN (ANALYZE, FORMAT JSON) SELECT id FROM memory_version m "
            "WHERE user_id = 'bench-0' AND status = 'active' "
            "AND recorded_range @> now() AND event_range @> now()"
        )
        plan = await cursor.fetchone()
        if plan is None:
            raise RuntimeError("explain_returned_no_plan")
        results["temporal_scan"] = plan[0][0]["Plan"]

        if args.report:
            staged = await benchmark_staged_report(
                connection, schema=schema, iterations=args.iterations, limit=args.limit
            )
            staged_end_to_end = cast(dict[str, float], staged["end_to_end"])
            staged_p95 = staged_end_to_end["p95_ms"]
            fused_p95 = fused["p95_ms"]
            results["staged_retrieval"] = staged
            results["g3_comparison"] = {
                "basis": {
                    "baseline_file": E002_BASELINE_FILE,
                    "baseline_tag": E002_BASELINE_TAG,
                    "dataset": (
                        f"rows_per_user={args.rows}, users={args.users}, "
                        f"limit={args.limit}, iterations={args.iterations}"
                    ),
                    "frozen_dense_p50_ms": E002_DENSE_P50_MS,
                    "frozen_fused_p95_ms": E002_FUSED_P95_MS,
                    "stricter_2x_limit_ms": E002_STRICTER_2X_LIMIT_MS,
                    "fused_2x_limit_ms": E002_FUSED_2X_LIMIT_MS,
                },
                "measured": {
                    "same_host_v1_fused_p95_ms": fused_p95,
                    "v2_staged_query_p50_ms": staged_end_to_end["p50_ms"],
                    "v2_staged_query_p95_ms": staged_p95,
                },
                "verdict": {
                    "v1_fused_vs_stricter_2x": (
                        "within_2x" if fused_p95 <= E002_STRICTER_2X_LIMIT_MS else "documented_regression"
                    ),
                    "v2_staged_vs_stricter_2x": (
                        "within_2x" if staged_p95 <= E002_STRICTER_2X_LIMIT_MS else "documented_regression"
                    ),
                    "v2_staged_vs_fused_2x": (
                        "within_2x" if staged_p95 <= E002_FUSED_2X_LIMIT_MS else "documented_regression"
                    ),
                },
            }

        rendered = json.dumps(results, indent=2, default=str)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0
    finally:
        await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        await connection.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
