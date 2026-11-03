"""Test the coding configuration against the currently wired, restricted-role API.

External episode captures are a deliberately narrower check than #136's literal
tool/file/event sources and repository-scope query-and-routing requirements.
"""

from pathlib import Path

import pytest

from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.runtime import Receipt
from custom_memory_engine.profile import load_profile
from tests.fixtures.coding_agent import coding_artifacts
from tests.integration.test_api_wiring_structure import caller, database, forbidden, rows, world  # noqa: F401

pytestmark = pytest.mark.integration
PROFILE = Path(__file__).resolve().parents[2] / "src" / "custom_memory_engine" / "profiles" / "coding.json"


@pytest.mark.parametrize("position", [0, 1, 2])
async def test_captured_episode_via_wired_api_uses_profile_retention(database, position: int) -> None:  # noqa: F811
    item = coding_artifacts()[position]
    async with world(database) as (conn, engine, _schema):
        register_ingest_handlers(engine, engine.resolver.runtime, load_profile(PROFILE))  # type: ignore[attr-defined]
        receipt = await engine.call(
            caller("alice"),
            "ingest_episode",
            {
                "scope": "/u/alice/",
                "source": {
                    "type": "external",
                    "ref": item.source.ref,
                    "span": item.source.span,
                    "content_hash": item.source.content_hash,
                },
                "content": item.content,
                "event_time": item.event_time.isoformat(),
                "idempotency_key": item.name,
            },
        )
        assert isinstance(receipt, Receipt)
        assert await rows(
            conn, "SELECT kind, lifecycle, retention, scope_path FROM memory_version WHERE id = %s", (receipt.id,)
        ) == [("episode", "observed", "session", "/u/alice/")]
        assert await rows(
            conn, "SELECT source_type, source_ref FROM memory_evidence WHERE version_id = %s", (receipt.id,)
        ) == [("external", item.source.ref)]


@pytest.mark.parametrize("position", [0, 1, 2])
async def test_literal_coding_sources_are_currently_rejected(database, position: int) -> None:  # noqa: F811
    item = coding_artifacts()[position]
    async with world(database) as (conn, engine, _schema):
        register_ingest_handlers(engine, engine.resolver.runtime, load_profile(PROFILE))  # type: ignore[attr-defined]
        error = await forbidden(
            engine,
            caller("alice"),
            "ingest_episode",
            {
                "scope": "/u/alice/",
                "source": {
                    "type": item.source.source_type,
                    "ref": item.source.ref,
                    "span": item.source.span,
                    "content_hash": item.source.content_hash,
                },
                "content": item.content,
                "event_time": item.event_time.isoformat(),
                "idempotency_key": item.name,
            },
        )
        assert (error.code, error.detail) == ("validation_error", "source_type_not_declared")
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(0,)]
