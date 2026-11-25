# Deployment (W3.6, V2-D117, V2-D119): host, worker, maintenance, database

Four processes and one database. This file covers the operational processes a deployment of this engine runs; the
database is PostgreSQL 16 with `pgvector`, `btree_gist`, and `pg_trgm`, provisioned by migrations (see
`src/custom_memory_engine/schema/README.md`).

```
app (ChronaMind FastAPI, port 8000)
  └── HTTP POST /v1/call ──────────────► cme-host  (uvicorn, port 8100)   ← connection pool + limits + /metrics
                                              │
                                              ├── PostgreSQL 16 (pgvector, btree_gist, pg_trgm)
                                              │        ▲
   LLM provider (extraction, adjudication) ────┼────────┤
                                              │        │
                                        cme-worker ────┤   ← the slow lane; drives the `job` table
                                                       │
                                  cme-maintenance ─────┘   ← daily timer: expire, purge_working, rebuild_index
```

The host answers reads and fast writes; the worker does the slow writes (model extraction and `index_rebuild` jobs);
maintenance periodically expires stale abstractions, purges working episodes, and enqueues index rebuilds. None
imports the others — they coordinate exclusively through PostgreSQL.

## What each environment variable means

| variable | who needs it | what it does |
|---|---|---|
| `CME_DATABASE_URL` | host, worker, maintenance | owner-level DSN. The runtime role is dropped to per transaction (`security.tenant_scope`); the worker keeps the owner only for `REINDEX ... CONCURRENTLY`, which needs the index owner |
| `CME_RUNTIME_ROLE` | host, worker, maintenance, probe | the restricted, `NOBYPASSRLS` role every tenant statement runs as — including every slow-lane write, maintenance call, and the health probe's queue read (`V2-D106`) |
| `CME_SCHEMA` | host, worker, maintenance | the engine schema (default `cme_engine`) |
| `CME_PROVENANCE_KEY`, `CME_DELETION_RECEIPT_KEY` | host | ≥32-char process-wide keys; a random one per restart breaks receipt replay. Backed up separately from database dumps (`V2-D119`) |
| `CME_HOST_TOKENS` | host | JSON `{token: {principal, tier, admin, delegate}}`. Tokens are the *only* source of tier. `delegate: true` marks a service token (the FastAPI app's) that names the end user per request in the envelope's `principal`; it cannot be `admin` and can never act as a principal that owns a token (`V2-D120`). Never logged (the registry's repr shows a count) |
| `CME_HOST_HOST`, `CME_HOST_PORT` | host | bind host and port (default `127.0.0.1:8100`) |
| `CME_HOST_MAX_BODY_BYTES` | host | maximum request body bytes enforced before JSON parsing (default `1048576`) |
| `CME_HOST_POOL_MIN`, `CME_HOST_POOL_MAX` | host | psycopg async pool bounds (default `1`..`8`) |
| `CME_HOST_RATE_LIMIT_RPS`, `CME_HOST_RATE_LIMIT_BURST` | host | per-principal token-bucket rate limiter (`0.0` disables by default; burst default `20`; `V2-D117`) |
| `CME_HOST_MAX_INFLIGHT`, `CME_HOST_PER_PRINCIPAL_MAX_INFLIGHT`, `CME_HOST_INFLIGHT_TIMEOUT_SECONDS` | host | global & per-principal in-flight semaphores (`pool_max >= max_inflight + 1` enforced at startup so `/healthz` and `/metrics` always retain a connection; per-principal defaults to `max(1, max_inflight - 1)` when unset so one caller cannot starve another; timeout default `0.05`s, returning `429 resource_limit`) |
| `CME_TAU_CONFIDENCE`, `CME_TAU_COVERAGE` | host | soundness admission defaults (`0.90` and `0.60`, `V2-D30`) |
| `CME_WORKER_USERS` | worker, maintenance, probe | the principals served — the queue is tenant-scoped, so this is the workers' and scheduler's whole remit |
| `CME_LLM_BASE_URL`, `CME_LLM_API_KEY`, `CME_LLM_PROTOCOL` | worker | the provider (credential from env file only) and wire protocol: Claude on `/v1/messages`, GPT on `/v1/chat/completions` |
| `CME_EXTRACTOR_MODEL`, `CME_ADJUDICATOR_MODEL`, `CME_ENTITY_ADJUDICATOR_MODEL` | worker | measured selection per capability (`reports/model_selection/README.md`); entity adjudicator falls back to relation adjudicator when unset |
| `CME_ADJUDICATE` | worker | `0` disables both adjudicators; default on |
| `CME_WORKER_IDLE_POLL` | worker | seconds between polls when idle (default `5`); also the cadence at which expired leases are returned |
| `CME_MAINTENANCE_INTERVAL_SECONDS` | maintenance | loop interval in seconds when `run_maintenance.py` runs without `--once` (default `86400`) |
| `CME_MAINTENANCE_INDEXES` | maintenance | comma-separated logical indexes to enqueue (`fts,vector,closure_hash`) |
| `CME_MAINTENANCE_PROFILE` | maintenance | optional path to a JSON/TOML profile overriding the default `V2-D14` expiry windows |
| `CME_RERANKER_ASSET`, `CME_RERANKER_DIR`, `CME_RERANKER_FLOOR` | host | relevance judge (`V2-D104`): `bge-reranker-v2-m3`, or unset to return the fused page unjudged (default floor `-8.8`) |
| `CME_EMBEDDING_ASSET`, `CME_EMBEDDING_DIR` | host, worker | local embedding model, or unset for lexical-only. **Both must agree** (`V2-D98`) |
| `CME_MODEL_CACHE` | compose | optional host directory mounted read-only at `/models` in `deploy/compose.worker.yaml` (default `../.model-cache`) |

Secrets live in `/etc/chronamind/worker.env` (mode `0600`, owned by `chronamind`) and in the host's equivalent. They
are never in units, never in container images, and never in the repository — `.env`, `.env.*`, and `.model-cache/` are
excluded by both `.gitignore` and `.dockerignore`.

## Transport rate limits, concurrency, and metrics (`V2-D117`, `V2-D119`)

- **Rate limiting and in-flight isolation (`host/limits.py`)**: Every `/v1/call` passes a per-principal token bucket
  (`CME_HOST_RATE_LIMIT_RPS` / `CME_HOST_RATE_LIMIT_BURST`) and bounded semaphore acquisition
  (`CME_HOST_MAX_INFLIGHT` / `CME_HOST_PER_PRINCIPAL_MAX_INFLIGHT`). Excess requests fail fast with HTTP `429`
  (`{"error": {"code": "resource_limit", ...}}`) with static codes only and zero caller content. Startup refuses any
  configuration where `pool_max < max_inflight + 1`.
- **Prometheus `/metrics` (`host/metrics.py`)**: Dependency-free Prometheus v0.0.4 exposition on `GET /metrics`
  (unauthenticated like `/healthz`; binds `127.0.0.1:8100` by default and bypasses the request concurrency semaphore on
  reserved pool capacity). Metrics cover `cme_host_calls_total`, `cme_host_call_duration_seconds`,
  `cme_host_rate_limited_total`, `cme_queue_depth`, `cme_queue_dead_letters_total`, and `cme_degradations_total`.
  Tenant identifiers, tokens, paths, queries, and memory content are strictly forbidden in all metric labels.
  `prometheus/prometheus.yml` scrapes `custom-memory-engine` and `grafana/dashboards/json/cme_operations.json` plots
  these series.

## Running the worker and maintenance scheduler

**systemd** — `deploy/cme-worker.service`, `deploy/cme-maintenance.service`, `deploy/cme-maintenance.timer`. Install:

```bash
sudo install -m 0644 deploy/cme-worker.service deploy/cme-maintenance.service deploy/cme-maintenance.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cme-worker cme-maintenance.timer
journalctl -u cme-worker -f          # structured JSON: one line per tick, one per failure
sudo systemctl stop cme-worker       # SIGTERM: finish the job in flight, then exit
```

Note on systemd hardening: `cme-worker.service` mounts `.model-cache` read-only (`ReadOnlyPaths=`), runs with
`ProtectSystem=strict`, `NoNewPrivileges=true`, and `PrivateTmp=true`, and deliberately avoids `ExecStartPost` health
checks so a temporarily degraded backlog does not cause systemd to kill-loop a healthy worker. Monitor health out of
band via `scripts/worker_healthcheck.py`.

**Docker** — `deploy/Dockerfile.worker` and `deploy/compose.worker.yaml`:

```bash
docker compose -f deploy/compose.worker.yaml --env-file /etc/chronamind/worker.env up -d
docker compose -f deploy/compose.worker.yaml ps      # `healthy` is the queue-based probe, not a PID
```

The worker container image pins both `python:3.12.16-slim-bookworm` and `uv` by immutable SHA-256 digest, installs
runtime extras with `uv sync --frozen --no-default-groups --extra host --extra embeddings`, keeps `/app/src`,
`/app/scripts`, and `/app/.venv` owned by `root:root` read-only, and runs as unprivileged `chronamind` (uid `10001`).

**What is executed in CI vs. verified locally / out-of-band.**
- **Executed in CI (`deploy-smoke` job in `.github/workflows/custom-memory-engine.yaml`, `#253`):** `deploy/ci_smoke.sh`,
  which is also the local command (`CME_TEST_DATABASE_URL=postgresql://… bash deploy/ci_smoke.sh` from
  `custom-memory-engine/`; it needs Docker with the compose plugin, `systemd-analyze`, and an owner-level URL):
  1. `scripts/verify_deploy_artifacts.py --require-systemd`: `systemd-analyze verify` on `cme-worker.service`,
     `cme-maintenance.service` and `cme-maintenance.timer` (non-zero on any unit error), plus the check that every
     `CME_*` variable a unit, the compose file or the Dockerfile names is in `.env.example` and in this file.
     `systemd-analyze verify` refuses an `ExecStart` binary that does not exist, so the units are verified against a
     staged `--root` holding `/opt/chronamind/custom-memory-engine/.venv/bin/python` and `/etc/chronamind/worker.env`
     at exactly the paths the units name; the units themselves are copied unchanged.
  2. `docker build -f deploy/Dockerfile.worker` from `custom-memory-engine/`.
  3. Image inspection: `Config.User` is `chronamind` and `id -u` in the image is `10001`; no secret-named variable in
     `Config.Env` or in any layer's history; `/app` holds only `.venv`, `scripts`, `src`; no `.env`, `.env.*`,
     `.model-cache`, `.git`, `PLAN` anywhere on the image filesystem, no tests, no host bytecode, an empty `/models`;
     code and venv not writable by the runtime user. A derived image that breaks each rule must fail the same
     inspection (the control).
  4. A throwaway schema migrated by `python -m custom_memory_engine.schema.migrate apply` and its role provisioned by
     `scripts/provision_role.py`; both are dropped when the script exits.
  5. The image against that database (host network, read-only root, `/tmp` tmpfs): the worker starts and serves eight
     idle polls with no failed tick; `scripts/worker_healthcheck.py` exits `0` on the empty queue and `2` on an
     unreachable database (the control); `docker stop` (`SIGTERM`) exits `0` after `slow_lane_stopped`; and each of
     `CME_DATABASE_URL`, `CME_RUNTIME_ROLE`, `CME_WORKER_USERS`, `CME_LLM_BASE_URL`, `CME_LLM_API_KEY`,
     `CME_EXTRACTOR_MODEL` missing exits `2` with `<name>_required` on stderr and no configured value.
  6. `docker compose -f deploy/compose.worker.yaml` builds through the file's own `build:`, then `up -d` and polls until
     the file's healthcheck reports `healthy`. A one-file override sets only the image tag and `network_mode: host`
     (so the container reaches the test database on `localhost`); healthcheck, `read_only`, `tmpfs`, `env_file`,
     interpolated variables, the `/models` volume and the grace period are the shipped file's.
- **Executed in CI (`verify` job):**
  - `tests/test_deploy_artifacts.py` runs `scripts/verify_deploy_artifacts.py` (cross-checks every `CME_*` env var across systemd units, compose, `.env.example`, and this document; inspects `Dockerfile.worker` and `.dockerignore`; and runs `systemd-analyze verify` against a staged `--root` on Linux hosts) plus mutation tests proving bogus `ExecStart` and unknown `CME_*` env vars fail loudly.
  - `tests/integration/test_worker_process_db.py` runs `scripts/run_worker.py` as a child process against PostgreSQL with `SIGTERM`, mid-call `SIGTERM`, `SIGKILL` lease recovery, and missing-env exit-2 assertions.
  - `tests/integration/test_slow_lane_concurrency_db.py` verifies `FOR UPDATE SKIP LOCKED`, concurrent `claim_one` / `recover_stale`, zombie worker lease fencing, and dead-lettering after `MAX_ATTEMPTS`.
  - `tests/integration/test_maintenance_scheduler_db.py` exercises `worker/maintenance.py` and `scripts/run_maintenance.py` (`--once`, `--dry-run`) against PostgreSQL.
  - `tests/integration/test_backup_restore_db.py` executes `scripts/rehearse_restore.py` (`pg_dump` / `pg_restore` round-trip, `BYPASSRLS` check, `public` extensions, role re-provisioning, pre-deletion resurrection) and the erasure-journal replay (`V2-D122`: export, a stale export
    that cannot shrink the journal, a wrong key refused, replay, a leak scan before and after, idempotence) against
    PostgreSQL.
  - `tests/integration/test_host_e2e_db.py` drives real HTTP requests over uvicorn for auth, rate/concurrency `429` isolation, and Prometheus `/metrics`.
- **Not executed in CI:**
  - systemd itself: no unit is started under a live PID 1, so `User=`, `EnvironmentFile=`, the sandboxing directives,
    `Restart=always` and the timer's schedule are checked by `systemd-analyze verify`, never run. The process contract
    the unit relies on (`SIGTERM` exit 0, `SIGKILL` lease recovery on restart, exit 2) is run by
    `test_worker_process_db.py` outside systemd.
  - A model call from the image: the smoke queue stays empty and `CME_LLM_BASE_URL` is never dialled. The model path is
    run with a stub provider in `test_worker_process_db.py`, outside the image.
  - The embedding model in the image: `CME_EMBEDDING_ASSET` is unset (lexical-only), so the ONNX runtime and a mounted
    `/models` cache with real weights are not loaded in CI.
  - The compose file's default networking: CI runs it with `network_mode: host`; reaching a database over a bridge
    network or another host is the deployment's configuration and is not exercised.
  - `cme-maintenance` (`scripts/run_maintenance.py`) in a container: it has no image of its own; it runs under
    systemd only, and as a process in `test_maintenance_scheduler_db.py`.

## Backup and restore policy (`A-014`, `V2-D119`, `V2-D122`)

Every PostgreSQL table in `cme_engine` enforces `FORCE ROW LEVEL SECURITY` (`app.user_id`), and signatures/receipts
depend on process-wide HMAC keys outside the database. Therefore:

1. **Back up with a `BYPASSRLS` (or `SUPERUSER`) role.** A dump taken by a regular or restricted role without
   `BYPASSRLS` either fails on forced RLS or silently omits tenant rows. Always verify the dump role's `rolbypassrls` /
   `rolsuper` attribute before dumping (`scripts/rehearse_restore.py` enforces this).
2. **Back up secrets separately from database dumps.** Store `CME_PROVENANCE_KEY`, `CME_DELETION_RECEIPT_KEY`, and
   `CME_HOST_TOKENS` in your secret manager / encrypted host backup (`/etc/chronamind/*.env`). They are never stored in
   PostgreSQL; restoring a database without the matching keys breaks signature and deletion-receipt verification.
3. **Bootstrap `public` extensions before restoring the schema.** Schema-scoped dumps (`--schema=cme_engine`) do not
   carry `public` extensions. Before running `pg_restore`, ensure `vector`, `btree_gist`, and `pg_trgm` exist in
   `public` (`CREATE EXTENSION IF NOT EXISTS ... WITH SCHEMA public`).
4. **Re-provision the runtime role and verify the catalog post-restore.** Roles are cluster-wide and not included in a
   schema dump. Restore a `--schema=cme_engine` dump with `pg_restore --no-owner --no-acl --exit-on-error` (omit
   `--schema=` on `pg_restore` so `CREATE SCHEMA` and `COMMENT ON SCHEMA` are restored), confirm the `cme_engine`
   schema comment carries `OWNER_MARKER`, run `security.provisioning.provision_runtime_role` to create/grant
   `CME_RUNTIME_ROLE` with least privilege, and run `MigrationRunner.status()` to verify `state == 'applied'`,
   `version == 32`, and matching `_cme_schema_migrations` checksums and catalog fingerprint.
5. **Deletions survive an older backup: the erasure journal (`V2-D122`).** A backup taken before a deletion still
   holds the deleted rows, and restoring it rolls back the `deletion_request` rows too. So the deletion log is copied
   *out of the database*: `cme-erasure-journal.timer` runs `scripts/erasure_journal.py export` every five minutes,
   merging every completed request (content-free: user id, scope, HMAC digest of the target, epoch) into
   `/var/lib/chronamind-journal/erasure-journal.jsonl`. The merge never drops an entry, so an export from a restored, older
   database cannot shrink it, and an exclusive lock keeps two exports from interleaving. It runs as its own transient
user (`DynamicUser=yes`, never the worker's `chronamind`) from `/etc/chronamind/journal.env`, which is `root:root`
`0600` (systemd reads it as root; the worker can neither read it nor the journal process's environment):
   - `CME_JOURNAL_DATABASE_URL`: a `BYPASSRLS` role that can only read `deletion_request`
     (`CREATE ROLE cme_journal LOGIN BYPASSRLS; GRANT USAGE ON SCHEMA cme_engine TO cme_journal;
     GRANT SELECT ON cme_engine.deletion_request TO cme_journal;`). Without `BYPASSRLS` it would read zero rows under
     forced RLS, so the export refuses (`journal_export_requires_bypassrls`);
   - `CME_DELETION_RECEIPT_KEY`: only a one-way key check is written, so a replay under another key is refused.

   **Ship the journal off-host** with every backup and continuously (it is small and holds no content); the database
   cannot protect a copy of its own history. **Restore procedure:** stop the host and worker; if the old database is
   still reachable, stop `cme-erasure-journal.timer` and run one last `export`; restore the backup (steps 3–4); then, before re-enabling traffic,

   ```bash
   CME_DATABASE_URL=... CME_RUNTIME_ROLE=... CME_DELETION_RECEIPT_KEY=... \
       python scripts/erasure_journal.py replay --journal /var/lib/chronamind-journal/erasure-journal.jsonl
   ```

   It re-applies, per user in epoch order and through the engine's own deletion path under the restricted role, every
   journalled request the restored database lacks, keeping the original request ids; a second run does nothing. A
   target the backup never held is recorded with nothing to erase. Then run `scripts/run_maintenance.py --once` so
   retention (`expire`, `purge_working`) catches up too. **What it does not cover:** deletions made after the newest
   journal copy you still have (the recovery point is the export interval plus your off-host shipping delay), and a
   journal lost together with the database.

Run a full local restore rehearsal at any time with:

```bash
uv run --env-file .env python scripts/rehearse_restore.py --source-dsn "$CME_TEST_DATABASE_URL"
```

## Failure behaviour, stated

| what happens | what the engine does |
|---|---|
| caller exceeds `CME_HOST_RATE_LIMIT_RPS` or `CME_HOST_MAX_INFLIGHT` | host fails fast with HTTP `429` (`resource_limit`), static error code only; `/healthz` and `/metrics` still succeed via reserved pool connection |
| the model returns 500 or times out | tenacity retries with exponential backoff; if it still fails, the job is re-queued with backoff at once (fenced on `status='running' AND attempts=%s`) |
| the model is down for the whole attempt budget | the job is `dead_letter` after `MAX_ATTEMPTS` (3), visible in `job` and `cme_queue_dead_letters_total`, and never retried silently |
| the worker is killed mid-extraction | the lease expires; `recover_stale` (`FOR UPDATE SKIP LOCKED`) returns jobs below `MAX_ATTEMPTS` to `queued` and dead-letters jobs at/above `MAX_ATTEMPTS`; stale zombie completions cannot overwrite newer attempts |
| the turn a job names was deleted | the read under the tenant's role finds nothing; the job fails, backs off and is dead-lettered after `MAX_ATTEMPTS` |
| the embedding model or judge fails on the host | reads degrade to lexical arms / unjudged fused order, report exact `arms`/`judgement`, and increment `cme_degradations_total` |
| the database is unreachable | the host answers `internal_error` (`500`, no content), `/healthz` answers `503`, the worker's probe exits `2`, and restart policies apply |

## What is *not* here

- **No load balancer config, TLS, or auth proxy.** The host binds `127.0.0.1` by default and authenticates bearer
  tokens itself; putting it behind TLS is the deployment's job.
- **No automatic replay on restore.** `scripts/erasure_journal.py replay` is a step of the restore procedure above; nothing
  detects a restore by itself.
