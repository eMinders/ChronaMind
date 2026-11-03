"""Test the coding configuration against the currently wired, restricted-role API.

External episode captures are a deliberately narrower check than #136's literal
tool/file/event sources and repository-scope query-and-routing requirements.
The routing checks classify the stored rows, not the fixture input axes.
"""

from pathlib import Path
from typing import cast

import pytest

from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.runtime import Receipt
from custom_memory_engine.engine.channels import channel_of
from custom_memory_engine.model.axes import Kind, Lifecycle
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
        async with engine.resolver.runtime.session("alice"):  # type: ignore[attr-defined]
            stored = await rows(conn, "SELECT kind, lifecycle FROM memory_version WHERE id = %s", (receipt.id,))
        assert len(stored) == 1, item.name
        kind, lifecycle = cast(Kind, stored[0][0]), cast(Lifecycle, stored[0][1])
        channel = channel_of(kind, lifecycle, "none", False, False)
        assert channel == item.expected_channel, f"{item.name}: expected {item.expected_channel}, got {channel}"


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


async def test_grounded_repository_claim_routes_from_stored_row(database) -> None:  # noqa: F811
    """The real turn fixture is asserted through the API before its stored axes are routed."""
    item = coding_artifacts()[3]
    async with world(database) as (conn, engine, _schema):
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, 's-alice', 'alice', 2, 'user', %s, %s)",
            (item.source.ref, item.source.content, item.event_time),
        )
        register_ingest_handlers(engine, engine.resolver.runtime, load_profile(PROFILE))  # type: ignore[attr-defined]
        receipt = await engine.call(
            caller("alice"),
            "assert",
            {
                "scope": "/u/alice/",
                "kind": "claim",
                "content": item.content,
                "evidence": [
                    {
                        "type": item.source.source_type,
                        "ref": item.source.ref,
                        "span": item.source.span,
                        "content_hash": item.source.content_hash,
                    }
                ],
                "event_range": None,
                "idempotency_key": item.name,
            },
        )
        assert isinstance(receipt, Receipt) and receipt.effect == "asserted", item.name
        async with engine.resolver.runtime.session("alice"):  # type: ignore[attr-defined]
            stored = await rows(
                conn, "SELECT kind, lifecycle, retention, scope_path FROM memory_version WHERE id = %s", (receipt.id,)
            )
            evidence = await rows(
                conn,
                "SELECT source_type, source_ref, evidence_span FROM memory_evidence WHERE version_id = %s",
                (receipt.id,),
            )
        assert stored == [("claim", "asserted", "durable", "/u/alice/")], item.name
        assert evidence == [("turn", item.source.ref, item.source.span)], item.name
        kind, lifecycle = cast(Kind, stored[0][0]), cast(Lifecycle, stored[0][1])
        channel = channel_of(kind, lifecycle, "none", False, False)
        assert channel == item.expected_channel, f"{item.name}: expected {item.expected_channel}, got {channel}"
