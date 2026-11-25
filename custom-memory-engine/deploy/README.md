# Deployment (W3.6): host, worker, database

Three processes and one database. This file is about the two processes a deployment of this engine actually runs; the
database is PostgreSQL 16 with `pgvector` and `pg_trgm`, provisioned by migrations (see `src/custom_memory_engine/schema/README.md`).

```
app (ChronaMind FastAPI, port 8000)
  └── HTTP POST /v1/call ──────────────► cme-host  (uvicorn, port 8100)   ← one process, a connection pool
                                              │
                                              ├── PostgreSQL 16 (pgvector, pg_trgm)
                                              │        ▲
   LLM provider (extraction, adjudication) ────┼────────┤
                                              │        │
                                        cme-worker ────┘   ← the slow lane; drives the `job` table
```

The host answers reads and fast writes; the worker does the slow writes (model extraction). They share exactly one
thing — the database — and neither imports the other. That is deliberate: the worker can crash, restart, or be absent
entirely while reads keep working, and a deployment that only ever writes through the API still behaves correctly with
no worker at all (claims are stored, extraction simply does not happen until one runs).

## What each environment variable means

| variable | who needs it | what it does |
|---|---|---|
| `CME_DATABASE_URL` | host, worker | owner-level DSN. The runtime role is dropped to per transaction (`security.tenant_scope`); the worker keeps the owner only for `REINDEX ... CONCURRENTLY`, which needs the index owner |
| `CME_RUNTIME_ROLE` | host, worker, probe | the restricted, `NOBYPASSRLS` role every tenant statement runs as — including every slow-lane write and the health probe's queue read (`V2-D106`) |
| `CME_SCHEMA` | host, worker | the engine schema (default `cme_engine`) |
| `CME_PROVENANCE_KEY`, `CME_DELETION_RECEIPT_KEY` | host | ≥32-char process-wide keys; a random one per restart breaks receipt replay |
| `CME_HOST_TOKENS` | host | JSON `{token: {principal, tier, admin}}`. Tokens are the *only* source of tier. Never logged (the registry's repr shows a count) |
| `CME_WORKER_USERS` | worker | the principals this worker serves — the queue is tenant-scoped, so this is the worker's whole remit |
| `CME_LLM_BASE_URL`, `CME_LLM_API_KEY`, `CME_LLM_PROTOCOL` | worker | the provider (the credential comes from the env file only) and which wire shape to speak: Claude on `/v1/messages`, GPT on `/v1/chat/completions`, inferred from the model family unless overridden |
| `CME_EXTRACTOR_MODEL`, `CME_ADJUDICATOR_MODEL`, `CME_ENTITY_ADJUDICATOR_MODEL` | worker | the measured selection per capability (`reports/model_selection/README.md`); the entity adjudicator defaults to the relation adjudicator when unset |
| `CME_ADJUDICATE` | worker | `0` disables both adjudicators (extraction only, cheaper, no semantic dedup and no cross-script entity merges). Default on |
| `CME_WORKER_IDLE_POLL` | worker | seconds between polls when idle (default 5); also the cadence at which expired leases are returned |
| `CME_RERANKER_ASSET`, `CME_RERANKER_DIR`, `CME_RERANKER_FLOOR` | host | the relevance judge (`V2-D104`): `bge-reranker-v2-m3`, or unset to return the fused page unjudged. The floor defaults to the measured `-8.8`; change it only after re-measuring |
| `CME_EMBEDDING_ASSET`, `CME_EMBEDDING_DIR` | host, worker | the local embedding model, or unset for a lexical-only deployment. **Both must agree**: a question encoded by one model cannot match a document encoded by another (`V2-D98`) |

Secrets live in `/etc/chronamind/worker.env` (mode 0600, owned by the service user) and in the host's equivalent. They
are never in the unit, never in an image, and never in the repository — `.env` and `.model-cache/` are gitignored.

## Running the worker

**systemd** — `deploy/cme-worker.service`. Install, then:

```bash
sudo systemctl enable --now cme-worker
journalctl -u cme-worker -f          # structured JSON: one line per tick, one per failure
sudo systemctl stop cme-worker       # SIGTERM: finish the job in flight, then exit
```

**Docker** — `deploy/Dockerfile.worker` and `deploy/compose.worker.yaml`:

```bash
docker compose -f deploy/compose.worker.yaml --env-file /etc/chronamind/worker.env up -d
docker compose -f deploy/compose.worker.yaml ps      # `healthy` is the queue-based probe, not a PID
```

Both paths do the same four things, which are the whole of the deployment contract:

1. **restart on failure** (`Restart=always` / `restart: unless-stopped`), with a five-second gap so a crash loop is
   visible in the journal rather than a fork bomb;
2. **stop gracefully** on `SIGTERM`: `scripts/run_worker.py` installs a handler, finishes the job in flight, and exits.
   The stop timeout (60 s) outlives one extraction with its retries;
3. **recover continuously**: `recover_stale` returns any job whose lease (`semantic.slow_lane.LEASE`, 10 minutes)
   expired, on start-up *and on every idle cycle*, so a job held by a worker that was killed is picked up by any live
   worker within one poll interval — not at the next restart;
4. **report health from the queue**, not from the process: `scripts/worker_healthcheck.py` reads each tenant's queue
   under the runtime role and exits `0` healthy, `1` degraded (the oldest due job is older than 30 minutes), `2`
   unhealthy (a lease expired for more than `STALE_LEASE_GRACE`, or the database is unreachable). A hung model call
   inside a live process is the failure this lane is most likely to have, and a PID check would pass through it.

**What is tested, and how.** `tests/integration/test_worker_process_db.py` runs the real `scripts/run_worker.py` as a
child process against a real database and a stub provider: a job is served end to end; `SIGTERM` exits 0; `SIGTERM`
*during* a model call still commits the job; `SIGKILL` mid-call leaves a held lease that a restarted process returns
and completes; a missing variable is exit 2 naming the variable. The loop itself (`custom_memory_engine.worker`) is
tested in-process with every model call checked to run **outside** a transaction (`test_worker_loop_db.py`). What is
still not executed is systemd or Docker itself; the unit and compose file are written to the contract those tests pin.

## Failure behaviour, stated

| what happens | what the engine does |
|---|---|
| the model returns 500 or times out | tenacity retries with exponential backoff; if it still fails, the job is re-queued with backoff at once (not left `running` until its lease expires) |
| the model is down for the whole attempt budget | the job is `dead_letter` after `MAX_ATTEMPTS` (3), visible in the table and in the journal, and never retried silently |
| the worker is killed mid-extraction | the lease expires; `recover_stale` returns the job to `queued`; the work is redone from the stored turn, which is why the job carries a turn id and not the text |
| the turn a job names was deleted | the read under the tenant's role finds nothing; the job fails, backs off and is dead-lettered after `MAX_ATTEMPTS` — never written against a turn that no longer exists |
| the embedding model is missing on the host | reads still answer from the two lexical arms and report exactly which arms ran (`QueryResult.arms`); nothing turns into a 500 |
| the embedding model is missing on the worker | claims are stored without vectors and logged; the lexical arms still find them |
| the database is unreachable | the host answers `internal_error` (no content), the worker's probe exits `2`, both restart policies apply |

## What is *not* here

- **No load balancer config, TLS, or auth proxy.** The host binds `127.0.0.1` by default and authenticates bearer
  tokens itself; putting it behind TLS is the deployment's job, and the engine does not pretend to do it.
- **No metrics exporter.** The host logs one structured line per call (operation, tier, status, code, duration) and the
  worker one per tick; a scrape endpoint is a separate decision, not a hidden one.
- **No scheduler for index maintenance.** `rebuild_index` enqueues work as any other job; a deployment that wants it on a
  schedule runs `curl` (host) or a cron unit.
