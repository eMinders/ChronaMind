"""The application imports and wires its routes; ``/metrics`` speaks the Prometheus text format."""

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.metrics import setup_metrics
from app.core.middleware import route_template
from app.main import app
from app.services.database import database_service


def test_the_application_imports_and_registers_its_routes() -> None:
    documented = set(app.openapi()["paths"])
    assert {"/health", "/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/chatbot/chat"} <= documented
    assert "/metrics" in {getattr(route, "path", None) for route in app.routes}


def test_metrics_are_exposed_in_the_prometheus_text_format() -> None:
    probe = FastAPI()
    setup_metrics(probe)
    response = TestClient(probe).get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "llm_inference_duration_seconds" in response.text


def test_metric_labels_are_route_templates_with_the_router_prefix_never_raw_ids() -> None:
    seen: list[str] = []
    inner = APIRouter()

    @inner.get("/sessions/{session_id}")
    async def session(session_id: str, request: Request) -> dict[str, str]:
        seen.append(route_template(request))
        return {}

    @inner.get("/")
    async def root(request: Request) -> dict[str, str]:
        seen.append(route_template(request))
        return {}

    outer = APIRouter()
    outer.include_router(inner, prefix="/auth")
    probe = FastAPI()
    probe.include_router(outer, prefix="/api/v1")

    @probe.get("/health")
    async def health(request: Request) -> dict[str, str]:
        seen.append(route_template(request))
        return {}

    client = TestClient(probe)
    for path in ("/api/v1/auth/sessions/0b5c", "/api/v1/auth/sessions/91f2", "/health", "/api/v1/auth/"):
        assert client.get(path).status_code == 200
    assert seen == [
        "/api/v1/auth/sessions/{session_id}",
        "/api/v1/auth/sessions/{session_id}",
        "/health",
        "/api/v1/auth/",
    ]


def test_the_database_layer_never_blocks_the_event_loop() -> None:
    """Every query is awaited on an async engine; a sync engine would stall all concurrent requests per query."""
    assert isinstance(database_service.engine, AsyncEngine)
    assert database_service.engine.url.drivername == "postgresql+psycopg"
