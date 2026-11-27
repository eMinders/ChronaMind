---
name: run-chronamind
description: Run, start, stop, smoke-test, or drive the ChronaMind FastAPI + LangGraph agent API (register/login/session/chat) on Windows. Use when asked to run the app, start the server, hit the API, or confirm a change works end to end.
---

ChronaMind is a FastAPI + LangGraph chatbot API backed by PostgreSQL/pgvector. It has no UI, so it is driven over HTTP. `driver.py` starts it detached on port 8765, then walks the real user flow: health, register, login, create session, chat with the LLM, read messages.

Paths are relative to the repo root (`ChronaMind/`). Verified on Windows 11 with Git Bash, `uv` 0.11, Python 3.13 and Docker Desktop.

## Prerequisites

- Postgres+pgvector reachable at the host/port in `.env.development` (here `localhost:5434`). `docker ps` should show the `chronamind-db-1` container healthy. If not: `docker compose up -d db`.
- `uv sync` already done (`.venv` exists).
- A working LLM key in `.env.development` (`DEFAULT_PROVIDER=openrouter` + `OPENROUTER_API_KEY`). Without one, only the `chat` step fails.
- Migrations applied: `uv run alembic upgrade head`.

## Run (agent path)

```bash
uv run python .claude/skills/run-chronamind/driver.py up      # starts server, waits for /health (about 30-60s cold)
uv run python .claude/skills/run-chronamind/driver.py smoke   # 8 PASS/FAIL steps, exit code 0 if all pass
uv run python .claude/skills/run-chronamind/driver.py status  # prints /health JSON
uv run python .claude/skills/run-chronamind/driver.py down    # kills the server process tree
```

- Server log: `.claude/skills/run-chronamind/.state/server.log`. Set `CHRONA_PORT` to change port 8765.
- `smoke` registers a throwaway `smoke-xxxx@example.com` user in the dev database each run. The chat step takes a few seconds to about a minute because it calls the real LLM.
- For ad-hoc calls reuse `call()` from `driver.py`, or use curl. Routes: `/health`, `/api/v1/auth/{register,login,session,sessions}`, `/api/v1/chatbot/{chat,chat/stream,messages}`. Login takes **form** fields (`email`, `password`, `grant_type=password`). Chat and messages need the **session** token from `/auth/session`, not the user token.

## Run (human path)

`run.py` / `make dev` (port 8000, reload) is the project's own path. I did not run it here. It does not apply the Avast workarounds below, so expect the SSL failures unless you launch through the driver.

## Gotchas (all hit on this machine, which runs Avast)

- **`OPENSSL_Uplink(...): no OPENSSL_Applink` and the process dies silently.** Avast sets `SSLKEYLOGFILE=\\.\aswMonFltProxy\...`. Python's `ssl.create_default_context()` tries to open it and aborts. urllib triggers it even for plain http. The driver pops the variable; for manual runs use `env -u SSLKEYLOGFILE ...`. It shows up on `import langchain_openai`, so the log stops after `database_initialized`.
- **`CERTIFICATE_VERIFY_FAILED` from Python while curl works.** Avast re-signs HTTPS with a root only the Windows store trusts. This breaks the tiktoken download at import time (`app/utils/graph.py`) and every LLM call (the failure surfaces as `APIConnectionError`, then a 60s "llm call timed out" 500 on `/chat`). `driver.py up` handles it two ways. It downloads the tiktoken file with curl into `TIKTOKEN_CACHE_DIR`. It also writes a `sitecustomize.py` on `PYTHONPATH` that points `certifi.where()` at a bundle of certifi plus the Windows ROOT/CA stores. `SSL_CERT_FILE` alone is not enough, because `langchain_openai` hardcodes `certifi.where()`.
- **`Psycopg cannot use the 'ProactorEventLoop'`** (health is fine, the LangGraph checkpointer pool spams errors, chat breaks). The plain `uvicorn` CLI does not set the Selector policy. `run.py` does, and so does the driver's launcher. Do not start with `uvicorn app.main:app` on Windows.
- **`uv run uvicorn ...` gives `uv trampoline failed to canonicalize script path`.** Use `uv run python -m uvicorn`, or just the driver.
- Backgrounding `uv run ... &` from the Bash tool keeps the call open until timeout. The driver uses a detached process with a log file instead.

## Troubleshooting

- `up` prints `server exited with ...`: read `.claude/skills/run-chronamind/.state/server.log` (strip colors with `sed 's/\x1b\[[0-9;]*m//g'`).
- `/health` shows `database: unhealthy`: the Postgres container is down or `POSTGRES_PORT` in `.env.development` is wrong.
- `chat` fails with `llm call timed out`: look in the log for `APIConnectionError` (TLS, see above) or a 401/429 from the provider (key or free-tier limits).
- `register` returns 429: `app/core/config.py` sets the register limit to 10 per hour. Wait, or restart the server (the limiter appears to be in-memory, since Redis is unavailable). I did not hit this.

## Tests and checks

There is no `tests/` directory for the app (the `custom-memory-engine/` subproject has its own). Use `uv run ruff check . && uv run pyright` (`make check`) for static checks.
