# ChronaMind

This repository holds two independent parts:

| Part | Where | What |
|---|---|---|
| **ChronaMind memory engine** (research and engine) | [`core/memory-engine/`](core/memory-engine/README.md) | The bi-temporal, write-governed, lineage-deletable memory engine, its benchmarks, evaluation harness and plans. The V2 plan is [`core/memory-engine/PLAN/V2/`](core/memory-engine/PLAN/V2/README.md). |
| **Product application** (this directory) | `apps/api/` | A FastAPI + LangGraph agent service, documented below. |

The two share no code, dependencies, database or runtime (`core/memory-engine/PLAN/INDEPENDENCE_BOUNDARY.md`);
each has its own CI workflow (`.github/workflows/README.md`).

---

# Product application (FastAPI + LangGraph agent service)

Built from a FastAPI + LangGraph agent template: stateful conversations, long-term memory, tool calling,
observability, rate limiting and auth. The LLM endpoint is any OpenAI-compatible API, configured through
`OPENAI_BASE_URL`, `OPENAI_API_KEY` and `DEFAULT_LLM_MODEL` (see `.env.example`).

## What's included

- **LangGraph** stateful agent with checkpointing, tool calling, and human-in-the-loop support
- **Long-term memory** via the core memory engine over HTTP — semantic search per user, cache-backed
- **LLM service** with circular model fallback, exponential backoff retries, and total timeout budget
- **Langfuse** tracing on all LLM calls; Prometheus metrics + Grafana dashboards
- **JWT auth** with session management; rate limiting via slowapi
- **Alembic** migrations; optional Valkey/Redis cache layer
- **Structured logging** with request/session/user context on every line

## Quickstart

### Option 1: One-Command Automated Setup (Recommended)

Clones, installs `uv`, creates `.env.development` with secure keys, starts PostgreSQL + `pgvector`, applies migrations, and launches the server:

- **Windows (PowerShell)**:
  ```powershell
  .\setup.ps1
  ```
- **macOS / Linux**:
  ```bash
  chmod +x setup.sh && ./setup.sh
  ```
- **Any OS (via uv / python)**:
  ```bash
  uv run --project apps/api python apps/api/scripts/setup.py
  ```

### Option 2: Docker Full Stack

```bash
git clone <repo-url> my-agent && cd my-agent
cp .env.example .env.development   # fill in your keys
make install
make docker-up                     # starts API + PostgreSQL
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) to see the interactive API.

> For local development guide see [apps/api/docs/getting-started.md](apps/api/docs/getting-started.md).

## Documentation

| Guide | What it covers |
|---|---|
| [Getting Started](apps/api/docs/getting-started.md) | Prerequisites, local setup, first API call |
| [Architecture](apps/api/docs/architecture.md) | System design, request flow, component diagrams |
| [Configuration](apps/api/docs/configuration.md) | All environment variables with defaults |
| [Authentication](apps/api/docs/authentication.md) | JWT flow, sessions, endpoint reference |
| [Database & Migrations](apps/api/docs/database.md) | Schema, Alembic migrations, pgvector |
| [LLM Service](apps/api/docs/llm-service.md) | Models, retries, fallback, timeout budget |
| [Memory](apps/api/docs/memory.md) | core memory engine integration, cache layer |
| [Observability](apps/api/docs/observability.md) | Langfuse, structured logging, Prometheus, profiling |
| [Evaluation](apps/api/docs/evaluation.md) | Eval framework, custom metrics, reports |
| [Docker](apps/api/docs/docker.md) | Docker, Compose, full monitoring stack |

## Monorepo structure

```text
core/memory-engine/     # Core memory domain, engine host, schema, research and governance (Python 3.12)
apps/api/               # FastAPI/LangGraph product adapter and API-owned persistence (Python 3.14)
apps/studio/            # React/TypeScript client (Node 22)
tools/demo/             # Cross-workspace demo orchestration
.github/                # CI/CD and review automation
```

The API and Studio integrate with the engine through its versioned HTTP contract. They do not import the engine package
or share its database, lockfile, or interpreter. This keeps the engine authoritative while allowing product adapters and
clients to evolve independently.

## Contributing

PRs welcome. Please read [apps/api/docs/getting-started.md](apps/api/docs/getting-started.md) to get your environment set up, then follow the coding conventions in [AGENTS.md](AGENTS.md).

Report security issues privately — see [SECURITY.md](SECURITY.md).

## License

See [LICENSE](LICENSE).

## FAQ

### General

**What is this template?**
A production-ready foundation for AI agent backends built on FastAPI + LangGraph. It bundles the components you'd otherwise wire up by hand: stateful conversations, long-term memory, tool calling, observability, rate limiting, and JWT auth.

**How does this differ from a basic LangGraph setup?**
The base LangGraph quickstart stops at "agent runs locally". This template adds Alembic migrations, the core memory engine over HTTP long-term memory, Langfuse tracing, Prometheus + Grafana dashboards, JWT sessions, slowapi rate limiting, structured logging with per-request context, and a circular-fallback LLM service — production concerns you'd otherwise build separately.

### Setup & Configuration

**Do I need Docker?**
Recommended but not required. `make docker-up` starts the API + PostgreSQL together. For local-only setup see [apps/api/docs/getting-started.md](apps/api/docs/getting-started.md).

**Which LLM providers are supported?**
Today: **OpenAI only** via the `LLMRegistry` in `apps/api/app/services/llm/registry.py`. Multi-provider support (Anthropic, Google, OpenRouter) via LangChain's `init_chat_model` is planned — see [#51](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template/issues/51). Configure your model via `DEFAULT_LLM_MODEL` in `.env.development`.

**How do I configure long-term memory?**
Long-term memory is self-hosted through the core memory engine sidecar and persists into your existing PostgreSQL via pgvector — there is no separate mem0 cloud account or API key. You only need a working `OPENAI_API_KEY` (used for fact extraction + embeddings) and the pgvector extension enabled. See [apps/api/docs/memory.md](apps/api/docs/memory.md) for details.

### Development

**How do I add a custom tool?**
Drop a LangChain `@tool`-decorated function in `apps/api/app/core/langgraph/tools/` and register it in the `tools` list exported from that package. The agent picks it up on next start; no graph changes needed.

**Can I run multiple agents (supervisor, sub-agents)?**
Yes. The template ships a single agent, but any compiled graph can be added as a node of the main graph. It shares `GraphState` (so `messages` flow through) and inherits the parent's Postgres checkpointer, so no extra persistence setup is needed. In `create_graph()` in `apps/api/app/core/langgraph/graph.py`:

```python
research = StateGraph(GraphState)
research.add_node("research", research_node)
research.set_entry_point("research")
research_agent = research.compile()  # no checkpointer: inherits the parent's

graph_builder.add_node("chat", self._chat, destinations=("tool_call", "research_agent", END))
graph_builder.add_node("research_agent", research_agent)
graph_builder.add_edge("research_agent", "chat")
```

`chat` then acts as the supervisor: return `Command(goto="research_agent")` to delegate, and the sub-agent's reply comes back to `chat`. Add one node per specialist. For a prebuilt version, see [`langgraph-supervisor`](https://github.com/langchain-ai/langgraph-supervisor-py).

**How does the LLM service handle failures?**
Two layers: (1) per-call exponential-backoff retry via `tenacity`, (2) **circular fallback** — if the active model exhausts its retries, the service rotates to the next model in `LLMRegistry` and continues. A total timeout budget caps the whole call so latency stays bounded. See [apps/api/docs/llm-service.md](apps/api/docs/llm-service.md).

**Can I use this without Langfuse?**
Yes. Set `LANGFUSE_TRACING_ENABLED=false` (or omit the Langfuse keys). The agent runs unchanged; structured logs still capture request/session/user context.

### Troubleshooting

**The API won't start**
- Ensure PostgreSQL is running (`make docker-up` brings it up alongside the API)
- Confirm `.env.development` exists — copy from `.env.example` and fill in required keys
- Apply migrations: `make migrate`

**Memory / semantic search returns nothing**
- Verify the `pgvector` extension is enabled in your PostgreSQL instance
- Confirm `OPENAI_API_KEY` is valid (mem0 calls OpenAI for fact extraction + embeddings)
- Check `MEMORY_ENGINE_URL` and `MEMORY_SLOT_CLASSIFIER_MODEL` are set in `.env.development`

**Rate limiting is too aggressive**
Limits are defined in `apps/api/app/core/limiter.py` (slowapi). Adjust per-route decorators or the default rate in that file. See [apps/api/docs/configuration.md](apps/api/docs/configuration.md) for the related env vars.
