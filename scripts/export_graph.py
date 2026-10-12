"""Export the LangGraph agent graph and template architecture to PNG and Mermaid formats."""

import asyncio
import base64
import shutil
import sys
from pathlib import Path

import httpx

# Enable SelectorEventLoop on Windows for async psycopg compatibility
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.v1.chatbot import agent  # noqa: E402

ARTIFACTS_DIR = Path(r"C:\Users\Lenovo\.gemini\antigravity-ide\brain\baeffff6-d0f4-464e-89b3-1a2d1b4cb01b")


def export_agent_graph(docs_dir: Path) -> None:
    """Compile the LangGraph agent and save diagram outputs."""
    print("Compiling LangGraph agent graph...")
    compiled_graph = asyncio.run(agent.create_graph())
    drawable_graph = compiled_graph.get_graph()

    mmd_path = docs_dir / "agent_graph.mmd"
    png_path = docs_dir / "agent_graph.png"

    # 1. Export Mermaid markdown
    mermaid_code = drawable_graph.draw_mermaid()
    mmd_path.write_text(mermaid_code, encoding="utf-8")
    print(f"Exported Agent Mermaid diagram to: {mmd_path}")

    # 2. Export PNG image
    try:
        png_bytes = drawable_graph.draw_mermaid_png()
        png_path.write_bytes(png_bytes)
        print(f"Exported Agent Graph PNG image to: {png_path}")
        if ARTIFACTS_DIR.exists():
            shutil.copy2(png_path, ARTIFACTS_DIR / "agent_graph.png")
            print(f"Copied Agent Graph PNG to artifacts: {ARTIFACTS_DIR / 'agent_graph.png'}")
    except Exception as e:
        print(f"Warning: Failed to fetch PNG from Mermaid renderer: {e}")


def export_template_architecture(docs_dir: Path) -> None:
    """Export the overall system template architecture diagram."""
    print("Exporting template system architecture diagram...")
    mmd_content = """graph TB
    Client["Client (HTTP / Streaming SSE)"]

    subgraph FastAPI["FastAPI Application"]
        MW["Middleware (Rate Limit, Metrics, Correlation ID, Profiler)"]
        Auth["JWT Authentication & Session Management"]
        API["REST API Endpoints (/chat, /chat/stream, /auth/*, /health)"]
    end

    subgraph AgentEngine["LangGraph State Engine"]
        Graph["StateGraph (chat <--> tool_call)"]
        Checkpointer["AsyncPostgresSaver (Multi-turn Checkpoint Store)"]
    end

    subgraph Services["Core Application Services"]
        LLM["LLM Service (Circular Fallback, Retries & Total Timeout)"]
        Memory["Memory Service (mem0 Semantic Memory + Cache)"]
        Tools["Tools Execution Pool (DuckDuckGo, Custom Tools)"]
    end

    subgraph Storage["Storage & Vector Infrastructure"]
        PG[("PostgreSQL 16 + pgvector (Data & Vectors)")]
        Cache["Valkey / Redis (Optional Memory Cache)"]
    end

    subgraph Observability["Observability & Telemetry"]
        Langfuse["Langfuse Tracing (Traces, Costs, Latencies)"]
        Prometheus["Prometheus Metrics + Grafana Dashboards"]
        Logs["structlog (Structured JSON / Console Context)"]
    end

    Client --> MW --> Auth --> API
    API --> Graph
    Graph --> LLM --> Langfuse
    Graph --> Tools
    Graph --> Memory --> Cache
    Graph <--> Checkpointer
    Memory --> PG
    Checkpointer --> PG
    API --> Prometheus
    API --> Logs
"""
    mmd_path = docs_dir / "template_architecture.mmd"
    png_path = docs_dir / "template_architecture.png"

    mmd_path.write_text(mmd_content, encoding="utf-8")
    print(f"Exported Template Architecture Mermaid diagram to: {mmd_path}")

    try:
        encoded = base64.urlsafe_b64encode(mmd_content.encode("utf-8")).decode("ascii")
        url = f"https://mermaid.ink/img/{encoded}"
        response = httpx.get(url, timeout=20.0)
        if response.status_code == 200:
            png_path.write_bytes(response.content)
            print(f"Exported Template Architecture PNG image to: {png_path}")
            if ARTIFACTS_DIR.exists():
                shutil.copy2(png_path, ARTIFACTS_DIR / "template_architecture.png")
                print(f"Copied Template Architecture PNG to artifacts: {ARTIFACTS_DIR / 'template_architecture.png'}")
        else:
            print(f"Warning: Mermaid renderer returned HTTP {response.status_code}")
    except Exception as e:
        print(f"Warning: Failed to render template architecture PNG: {e}")


def main() -> None:
    """Execute all diagram exports."""
    root_dir = Path(__file__).resolve().parent.parent
    docs_dir = root_dir / "docs"
    docs_dir.mkdir(exist_ok=True)

    export_agent_graph(docs_dir)
    export_template_architecture(docs_dir)


if __name__ == "__main__":
    main()
