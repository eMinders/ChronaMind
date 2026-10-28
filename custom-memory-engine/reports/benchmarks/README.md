# Measured benchmarks

Only numbers produced by a command in this repository appear here. Nothing is
estimated or extrapolated.

## Retrieval latency

Produced by `scripts/bench_retrieval.py`, which seeds a disposable schema, warms
the connection, then times repeated `rank_versions` calls and records the planner
choice for the temporal predicate.

```bash
docker compose --env-file .env -p cme-verify up -d --wait
CME_TEST_DATABASE_URL=... uv run python scripts/bench_retrieval.py \
  --rows 500 --users 2 --iterations 50
```

| File | Rows per user | Candidates |
|---|---:|---:|
| `retrieval_500.json` | 500 | 500 |
| `retrieval_2000.json` | 2000 | 2000 |

Machine-readable (`p50_ms`, `p95_ms`, `min_ms`, `max_ms` per channel, plus the
`EXPLAIN (ANALYZE, FORMAT JSON)` plan for the temporal predicate).

### Latest run

| Candidates | Channel | P50 (ms) | P95 (ms) |
|---:|---|---:|---:|
| 500 | dense | 10.52 | 12.00 |
| 500 | lexical | 4.17 | 4.68 |
| 500 | fused (RRF) | 10.59 | 12.00 |
| 2000 | dense | 26.99 | 29.41 |
| 2000 | lexical | 11.11 | 11.95 |
| 2000 | fused (RRF) | 28.16 | 29.84 |

### What these numbers are and are not

- They measure one connection, one user's candidate set, `limit=50`, on a
  disposable local PostgreSQL 16.15 container with no warm cache tuning.
- They are **not** a throughput or concurrency measurement. That is `RL-093`
  (8 workers, HNSW vs exact recall), which has not been run.
- They are **not** comparative against B0–B4. No baseline has been timed here.
- The dense channel is an exact scan, as `D-02` requires; HNSW is deliberately
  absent from `P`.

### Planner note

With 50 users × 40 rows the temporal predicate uses
`Bitmap Heap Scan` on `memory_version_temporal_idx`; with one user holding all
2000 rows PostgreSQL correctly prefers a sequential scan. Both plans are
expected — the seq scan is not a defect to "fix".

## Optimization history

Each change was measured, and one was rejected on correctness grounds.

| Change | Effect |
|---|---|
| Rank on ids, project payloads for the top-k only | removed ~25 µs/row projection cost |
| Dense channel: id array round-trip → single SQL join | 40–45 ms → 27 ms @2000 |
| Lexical: client-side sort → `ORDER BY … LIMIT` in SQL | 19 ms → 11.6 ms @2000 |
| `to_tsvector(content)` per row → generated `content_fts` | 6.44 ms → 2.04 ms |
| Bounded lexical candidate pool (**rejected**) | 13× faster but only 7/50 of the exact top-50 retained |
