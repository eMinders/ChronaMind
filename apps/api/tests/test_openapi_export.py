"""`docs/api/openapi.json` is the web interface's single source of API types, so it must equal the application.

A route or response model changed without re-running `scripts/export_openapi.py` fails here, in CI, instead of
reaching the interface as a shape it does not expect.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]


def exporter() -> ModuleType:
    """Load `scripts/export_openapi.py` (the scripts folder is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "export_openapi", ROOT / "apps" / "api" / "scripts" / "export_openapi.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_openapi_document_equals_the_application() -> None:
    module = exporter()
    committed = module.TARGET.read_text(encoding="utf-8")
    assert committed == module.render(), (
        "docs/api/openapi.json is stale: run `uv run python scripts/export_openapi.py`"
    )


def test_the_document_carries_every_memory_inspector_model_and_route() -> None:
    document = json.loads((ROOT / "apps" / "api" / "docs" / "api" / "openapi.json").read_text(encoding="utf-8"))

    schemas = set(document["components"]["schemas"])
    assert {
        "MemoryView",
        "MemoryItem",
        "MemoryItemResponse",
        "ProvenanceOut",
        "TimelineView",
        "ChangesView",
        "AdjudicateResult",
        "ForgetResult",
        "EngineHealth",
    } <= schemas
    assert {
        "/api/v1/memory/",
        "/api/v1/memory/health",
        "/api/v1/memory/search",
        "/api/v1/memory/timeline",
        "/api/v1/memory/changes",
        "/api/v1/memory/forget",
        "/api/v1/memory/{artifact_id}",
        "/api/v1/memory/{artifact_id}/provenance",
        "/api/v1/memory/{artifact_id}/adjudicate",
    } <= set(document["paths"])


def test_the_document_does_not_depend_on_the_environment() -> None:
    document = json.loads((ROOT / "apps" / "api" / "docs" / "api" / "openapi.json").read_text(encoding="utf-8"))
    assert document["info"] == {"title": "ChronaMind application API", "version": "generated"}
