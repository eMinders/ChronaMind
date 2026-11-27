# ChronaMind Live Demo Stack

One-command local stack for running the FastAPI + LangGraph app together with the ChronaMind memory engine host and both PostgreSQL databases.

## Prerequisites

1. **Root application environment** (CPython 3.14):
   ```bash
   uv sync
   ```
2. **Memory engine environment** (CPython 3.12, with the `host` extra):
   ```bash
   cd custom-memory-engine && uv sync --locked --extra host
   ```
3. **Docker** (running daemon; starts the isolated `engine-db` and `app-db` `pgvector` containers).
4. **LLM provider credentials** in `.env.development`, `.env`, or your shell environment (for example `OPENROUTER_API_KEY` + `DEFAULT_PROVIDER=openrouter`, or `OPENAI_API_KEY`) so chat turns and the semantic slot classifier can call a model.

## Commands

Run all commands from the repository root:

```bash
# 1. Start engine DB, engine host (:8100), app DB, and app (:8000)
uv run python scripts/demo/up.py

# 2. Prove the memory path end-to-end (register -> Berlin -> Paris -> single Paris fact)
uv run python scripts/demo/check.py

# 3. Stop app, engine host, and DB containers (keeps volumes and .env.demo keys)
uv run python scripts/demo/down.py
```

To remove the Docker database volumes, generated `.env.demo`, and `.state/` completely:

```bash
uv run python scripts/demo/down.py --wipe
```

## Ports and State

- App API: `http://127.0.0.1:8000` (`/health`)
- Engine host: `http://127.0.0.1:8100` (`/healthz`, lexical-only; no local model files needed)
- Engine DB / App DB: `127.0.0.1:5441` / `127.0.0.1:5442` (Docker Compose project `chronamind-demo`)
- Generated secrets (`MEMORY_ENGINE_TOKEN`, `MEMORY_ENGINE_USER_TOKEN`, `CME_PROVENANCE_KEY`, `CME_DELETION_RECEIPT_KEY`, `JWT_SECRET_KEY`, DB credentials) are written once to `scripts/demo/.env.demo` and reused across `down.py` / `up.py` cycles until `down.py --wipe`.
- Runtime PIDs, Docker Compose manifest, and logs live in `scripts/demo/.state/`. Both `.env.demo` and `.state/` are gitignored by `scripts/demo/.gitignore`.
