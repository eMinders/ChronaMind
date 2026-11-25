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
| `retrieval_stages_2000.json` | 2000 | 2000 (v1 + V2 five-stage `query` P50/P95, overhead, and G3 comparison) |

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

## Hybrid read path (`W3.5`, `E-043`, `V2-D86`) — the honest re-baseline

Produced by `scripts/bench_hybrid.py`, which builds the full migrated schema, seeds 2000 beliefs with
real content and a BAAI/bge-m3 vector in the 1024 slot, provisions the restricted runtime role, and times
`query` through `Engine.call` for three request shapes. Every number below is measured by that command into a
disposable schema; nothing is estimated.

```bash
docker compose --env-file .env -p cme-verify up -d --wait
uv run --env-file .env python scripts/bench_hybrid.py --rows 2000 --iterations 20 --report
```

| Configuration | Items rendered | `candidate_generation` P50 (ms) | Total P50 (ms) | Total P95 (ms) |
|---|---:|---:|---:|---:|
| recency scan (pre-`V2-D86`, for comparison) | 50 | 4.71 | 9.1 | 10.16 |
| lexical only (`'simple'` full-text + trigram) | 50 | 12.09 | 20.24 | 24.61 |
| hybrid (lexical + dense over the 1024 slot) | 50 | 32.73 | 37.24 | 38.39 |

**Why this replaces the G3 figure rather than adding to it.** `retrieval_stages_2000.json` records 11.82 ms P95 for a
`query` that performed **no** vector search, **no** lexical search and **no** ranking — it measured
`ORDER BY recorded_range DESC LIMIT n`, and comparing it with V1's 32.61 ms hybrid search was apples to oranges. The
recency row above reproduces that number (10.16 ms P95 here) so the difference is
visible rather than argued about: ranking by relevance costs about
28.2 ms P95 at this scale, and buys a read path
that answers a question.

**Against the gate.** G3's criterion is P95 within 2× the frozen 27 ms reference, i.e. ≤ 53.98 ms. The hybrid
configuration measures 38.39 ms P95, so the criterion still holds on the path that actually
ranks — with the arms named and `charged_calls = 0` (no generative model is called on this path).

**What these numbers are not.** They are one connection, one user's 2,000-row scope, `limit=50`, on the disposable local
container. They are not a throughput measurement, and they are not a statement about recall quality: the seeded vectors
are synthetic unit vectors, so this measures the query path, not an embedder. At this corpus size PostgreSQL may choose
a scan over the HNSW index, and the number reports what a deployment of this size actually runs.
