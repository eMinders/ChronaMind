"""Measured retrieval latency for the read path (no fabricated numbers).

Seeds N single-valued beliefs per user into a disposable schema, then times the
dense, lexical and fused paths plus the planner's use of the new indexes.
Reports P50/P95 over repeated iterations.

    uv run --env-file .env python scripts/bench_retrieval.py --rows 2000 --iterations 50
"""

import argparse
import asyncio
import os
import random
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from psycopg import AsyncConnection, sql

from custom_memory_engine.config import TestDatabaseConfig
from custom_memory_engine.engine.predicates import current_state
from custom_memory_engine.engine.retrieval import rank_versions

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "src/custom_memory_engine/schema/001_full_schema.sql"
MODEL = "BAAI/bge-small-en-v1.5"
WORDS = (
    "vegetarian fish morning run coffee tea guitar piano london berlin summer winter "
    "laptop python rust reading hiking cycling swimming painting chess photography"
).split()


def embedding(rng: random.Random) -> list[float]:
    values = [rng.gauss(0.0, 1.0) for _ in range(384)]
    norm = sum(value * value for value in values) ** 0.5
    return [value / norm for value in values]


async def seed(conn: AsyncConnection, rows: int, users: int) -> datetime:
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
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, valid_range, recorded_range) VALUES "
                    "(%s, %s, %s, 'active', 'user', %s, true, %s, tstzrange(%s, NULL, '[)'), "
                    "tstzrange(clock_now(), NULL, '[)'))",
                    (version_id, f"key-{index}", user, f"fact-{index}", content, start + timedelta(days=index % 365)),
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


async def timed(coro_factory, iterations: int) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = perf_counter()
        await coro_factory()
        samples.append((perf_counter() - started) * 1000.0)
    return {
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=2000, help="beliefs per user")
    parser.add_argument("--users", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=50)
    args = parser.parse_args()

    config = TestDatabaseConfig.from_environment(os.environ)
    schema = "cme_bench_" + uuid4().hex
    connection = await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    )
    try:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await connection.execute(SCHEMA_SQL.read_text(encoding="utf-8"), prepare=False)
        reference = await seed(connection, args.rows, args.users)
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
        results = {
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
            "fused": await timed(
                lambda: rank_versions(
                    connection,
                    current_state("bench-0", reference),
                    query_text="morning coffee",
                    query_embedding=query_embedding,
                    fusion="rrf",
                    schema_name=schema,
                ),
                args.iterations,
            ),
        }

        cursor = await connection.execute(
            "EXPLAIN (ANALYZE, FORMAT JSON) SELECT id FROM memory_version m "
            "WHERE user_id = 'bench-0' AND status = 'active' "
            "AND recorded_range @> now() AND valid_range @> now()"
        )
        plan = await cursor.fetchone()
        results["temporal_scan"] = plan[0][0]["Plan"]
        print(__import__("json").dumps(results, indent=2, default=str))
        return 0
    finally:
        await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        await connection.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
