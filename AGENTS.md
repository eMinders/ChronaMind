# AI Agent Development Guide — the FastAPI application

This document provides essential guidelines for AI agents working on this LangGraph FastAPI Agent project: `app/`, `evals/`,
`alembic/`, `docs/`, and the root packaging files. It is the FastAPI/LangGraph template's guide.

> **Scope.** This file governs the root **application** only (CPython 3.14, root `pyproject.toml`/`uv.lock`, `make check`).
> It does **not** apply to `custom-memory-engine/`, which is a separate project with its own interpreter (CPython 3.12),
> its own Ruff/Pyright/lock, its own CI job, and its own instructions in
> [`custom-memory-engine/CLAUDE.md`](custom-memory-engine/CLAUDE.md). A rule below is not evidence about the engine, and
> must never be used to review an engine change.
>
> Two rules below are already **false in the code this file governs**, and are marked where they appear: the import rule
> (both projects use deliberate guarded/lazy imports) and the rate-limiter rule (four `app/api/v1/auth.py` session routes
> have none). Do not enforce either retroactively; do not extend the import rule to new lazy imports without a reason.

## First-Time Setup & Installation

Run one of the automated setup scripts to bootstrap the repository (downloads `uv`, creates `.env.development` with secure keys, starts PostgreSQL with `pgvector`, syncs dependencies, installs pre-commit hooks, applies migrations, and boots the server):

- **Windows (PowerShell)**:
  ```powershell
  .\setup.ps1
  ```
- **macOS / Linux**:
  ```bash
  chmod +x setup.sh && ./setup.sh
  ```
- **Cross-Platform / CLI**:
  ```bash
  uv run python scripts/setup.py
  # or
  make setup
  ```

## Quick Commands

```bash
make setup                # Automated first-time repo bootstrap
make install              # Install deps (uv sync) + pre-commit hooks
make dev                  # Dev server with hot reload (port 8000 via run.py)
make lint                 # ruff check .
make format               # ruff format .
make typecheck            # uv run pyright (static type check)
make test                 # root app tests (tests/, no services needed)
make check                # lint + format-check + typecheck + test (= CI `checks`)
make eval                 # Run LLM evals (interactive)
make eval-quick           # Run LLM evals (default settings)
make migrate              # Run DB migrations to latest (Alembic)
make docker-up            # Docker: API + DB (ENV=development by default)
make stack-up ENV=development  # Full stack: API + DB + Prometheus + Grafana
```

> All server/DB/Docker targets accept `ENV=development|staging|production|test`.
> Run `make help` for the full list of targets.

## Project Structure

```
app/
  api/v1/          # Route handlers (auth.py, chatbot.py, api.py)
  core/
    config.py      # Pydantic Settings config
    database.py    # Async DB setup
    langgraph/     # LangGraph agent graph + tools
    logging.py     # structlog setup
    llm.py         # LLM service with retry logic
    limiter.py     # Rate limiting (slowapi)
    metrics.py     # Prometheus metrics
    middleware.py  # ASGI middleware
    prompts/       # System prompts
  models/          # SQLModel ORM models
  schemas/         # Pydantic request/response schemas + graph state
  services/        # Business logic services
  utils/           # Shared utilities
evals/             # LLM evaluation framework (Langfuse-based)
scripts/           # Environment setup, Docker build scripts
```

## Project Overview

This is a production-ready AI agent application built with:
- **LangGraph** for stateful, multi-step AI agent workflows
- **FastAPI** for high-performance async REST API endpoints
- **Langfuse** for LLM observability and tracing
- **PostgreSQL + pgvector** for long-term memory storage (mem0ai)
- **JWT authentication** with session management
- **Prometheus + Grafana** for monitoring

## Quick Reference: Critical Rules

### Import Rules
- Keep imports at the top of the file, **except for an optional dependency**: `app/core/cache.py` (redis),
  `app/core/middleware.py` (pyinstrument) and `app/services/llm/router.py` (provider SDKs) import inside a
  `TYPE_CHECKING`/`try` block on purpose, so the application runs without that extra installed. A guarded optional
  import is the one correct place for an import below the top, and it needs its `# pyright: ignore[reportMissingImports]`.
- The memory engine's deliberate lazy imports are **not** covered by this rule, and are not violations of it.

### Logging Rules
- Use **structlog** for all logging
- Log messages must be **lowercase_with_underscores** (e.g., `"user_login_successful"`)
- **NO f-strings in structlog events** - pass variables as kwargs
- Use `logger.exception()` instead of `logger.error()` to preserve tracebacks
- Example: `logger.info("chat_request_received", session_id=session.id, message_count=len(messages))`

### Retry Rules
- **Always use tenacity library** for retry logic
- Configure with exponential backoff
- Example: `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))`

### Output Rules
- **Always enable rich library** for formatted console outputs
- Use rich for progress bars, tables, panels, and formatted text

### Caching Rules
- **Only cache successful responses**, never cache errors
- Use appropriate cache TTL based on data volatility

### FastAPI Rules
- **New** routes must have rate limiting decorators (`@limiter.limit(settings.RATE_LIMIT_ENDPOINTS[...])`). The four
  `app/api/v1/auth.py` session routes (`POST /session`, `PATCH /session/{id}/name`, `DELETE /session/{id}`,
  `GET /sessions`) predate this rule and do not have one: adding it is a behavioural change for existing clients, so it
  is a tracked decision, not a drive-by fix.
- Use dependency injection for services, database connections, and auth
- All database operations must be async

## Code Style Conventions

### Python/FastAPI
- Use `async def` for asynchronous operations
- Use type hints for all function signatures
- Prefer Pydantic models over raw dictionaries
- Use functional, declarative programming; avoid classes except for services and agents
- File naming: lowercase with underscores (e.g., `user_routes.py`)
- Use the RORO pattern (Receive an Object, Return an Object)

### Error Handling
- Handle errors at the beginning of functions
- Use early returns for error conditions
- Place the happy path last in the function
- Use guard clauses for preconditions
- Use `HTTPException` for expected errors with appropriate status codes

## LangGraph & LangChain Patterns

### Graph Structure
- Use `StateGraph` for building AI agent workflows
- Define clear state schemas using Pydantic models (see `app/schemas/graph.py`)
- Use `CompiledStateGraph` for production workflows
- Implement `AsyncPostgresSaver` for checkpointing and persistence
- Use `Command` for controlling graph flow between nodes

### Tracing
- Use LangChain's `CallbackHandler` from Langfuse for tracing all LLM calls
- All LLM operations must have Langfuse tracing enabled

### Memory (the ChronaMind engine, over HTTP)

Long-term memory is the engine in `custom-memory-engine/`, reached only over `POST {MEMORY_ENGINE_URL}/v1/call`
(`app/services/memory_engine.py`, `V2-D93`). The `mem0ai` client is retired: never import or extend it. The full
reference is [`docs/memory.md`](docs/memory.md).

**Two engine tokens, two clients, never swapped.** Both are delegating tokens on the engine host, which take their tier
from the token and act for the end user named as `principal` (`V2-D120`, threat `T-42`).

| Setting | Tier | Client | Used by |
| --- | --- | --- | --- |
| `MEMORY_ENGINE_TOKEN` | B | `memory_engine` | the agent only: `search()` before a reply, `add()` after it |
| `MEMORY_ENGINE_USER_TOKEN` | C | `memory_inspector` | the `/api/v1/memory/*` routes only (`app/api/v1/memory.py`) |

- Tier C can `delete`, `adjudicate`, `export` and run `audit_as_of` for **every** end user, which Tier B cannot. Never give
  the agent path the Tier C token, and never add an agent or tool call that uses `memory_inspector`. A test
  (`tests/test_memory_inspector_client.py`) fails if the agent's client sends the inspector token.
- The `principal` is always `str(user.id)` from the authenticated user (`get_current_user`), never a value taken from the
  request. No request field may name a principal or a tier.
- An empty `MEMORY_ENGINE_USER_TOKEN` disables the inspector routes (HTTP 503 `inspector_not_configured`). The agent's
  memory is unaffected.
- Real token values live only in the gitignored `.env*` files and in the engine host's `CME_HOST_TOKENS`.
  `.env.example` carries the key with an empty value. Never commit a real one.
- A new or changed inspector route or response model needs `uv run python scripts/export_openapi.py`; CI fails while
  `docs/api/openapi.json` is stale.

## Authentication & Security

- Use JWT tokens for authentication
- Implement session-based user management (see `app/api/v1/auth.py`)
- Use `get_current_session` dependency for protected endpoints
- Store sensitive data in environment variables
- Validate all user inputs with Pydantic models

## Database Operations

- Use SQLModel for ORM models (combines SQLAlchemy + Pydantic)
- Define models in `app/models/` directory
- Use async database operations with asyncpg
- Use LangGraph's AsyncPostgresSaver for agent checkpointing

## Performance Guidelines

- Minimize blocking I/O operations
- Use async for all database and external API calls
- Implement caching for frequently accessed data
- Use connection pooling for database connections
- Optimize LLM calls with streaming responses

## Observability

- Integrate Langfuse for LLM tracing on all agent operations
- Export Prometheus metrics for API performance
- Use structured logging with context binding (request_id, session_id, user_id)
- Track LLM inference duration, token usage, and costs

## Testing & Evaluation

- Implement metric-based evaluations for LLM outputs (see `evals/` directory)
- Create custom evaluation metrics as markdown files in `evals/metrics/prompts/`
- Use Langfuse traces for evaluation data sources
- Generate JSON reports with success rates

## Configuration Management

- Use environment-specific configuration files (`.env.development`, `.env.staging`, `.env.production`)
- Use Pydantic Settings for type-safe configuration (see `app/core/config.py`)
- Never hardcode secrets or API keys

## Key Dependencies

- **FastAPI** - Web framework
- **LangGraph** - Agent workflow orchestration
- **LangChain** - LLM abstraction and tools
- **Langfuse** - LLM observability and tracing
- **Pydantic v2** - Data validation and settings
- **structlog** - Structured logging
- **mem0ai** - Long-term memory management
- **PostgreSQL + pgvector** - Database and vector storage
- **SQLModel** - ORM for database models
- **tenacity** - Retry logic
- **rich** - Terminal formatting
- **slowapi** - Rate limiting
- **prometheus-client** - Metrics collection

## 10 Commandments for This Project

1. Every **new** route has a rate limiting decorator (the four legacy `auth.py` session routes are the known exception)
2. All LLM operations must have Langfuse tracing
3. All async operations must have proper error handling
4. All logs must follow structured logging format with lowercase_underscore event names
5. All retries must use tenacity library
6. All console outputs should use rich formatting
7. All caching should only store successful responses
8. Imports stay at the top of a file, except a guarded import of an optional dependency (see Import Rules)
9. All database operations must be async
10. All endpoints must have proper type hints and Pydantic models
11. All code must pass `make typecheck` (pyright standard mode)

## Common Pitfalls to Avoid

- ❌ Using f-strings in structlog events
- ❌ Adding imports inside functions
- ❌ Forgetting rate limiting decorators on a **new** route (see FastAPI Rules for the four legacy exceptions)
- ❌ Missing Langfuse tracing on LLM calls
- ❌ Caching error responses
- ❌ Using `logger.error()` instead of `logger.exception()` for exceptions
- ❌ Blocking I/O operations without async
- ❌ Hardcoding secrets or API keys
- ❌ Missing type hints on function signatures

## When Making Changes

Before modifying code:
1. Read the existing implementation first
2. Check for related patterns in the codebase
3. Ensure consistency with existing code style
4. Add appropriate logging with structured format
5. Include error handling with early returns
6. Add type hints and Pydantic models
7. Verify Langfuse tracing is enabled for LLM calls

## References

- LangGraph Documentation: https://langchain-ai.github.io/langgraph/
- LangChain Documentation: https://python.langchain.com/docs/
- FastAPI Documentation: https://fastapi.tiangolo.com/
- Langfuse Documentation: https://langfuse.com/docs
