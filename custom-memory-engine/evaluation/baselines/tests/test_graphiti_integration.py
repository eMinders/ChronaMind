"""Actual pinned Graphiti SDK; scripted model boundaries, disposable FalkorDB."""

import asyncio
import importlib
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from evaluation.baselines.contracts import ANSWER_MODEL

os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
pytest.importorskip("graphiti_core", reason="install the locked baselines extra for Graphiti integration")
worker_module = importlib.import_module("evaluation.baselines.b4_graphiti")
dangling = importlib.import_module("graphiti_core.utils.maintenance.dangling_endpoints")
combined = importlib.import_module("graphiti_core.utils.maintenance.combined_extraction")
nodes_module = importlib.import_module("graphiti_core.nodes")
graphiti_module = importlib.import_module("graphiti_core.graphiti")


def test_all_loaded_graphiti_clock_aliases_use_replay_clock() -> None:
    worker_module.install_pinned_shims()
    clock = datetime(2020, 3, 4, tzinfo=UTC)
    token = worker_module._CLOCK.set(clock)
    try:
        assert dangling.utc_now() == clock
        assert combined.utc_now() == clock
        assert nodes_module.EntityNode(name="Ada", group_id="fixture").created_at == clock
    finally:
        worker_module._CLOCK.reset(token)


@pytest.mark.asyncio
async def test_failed_operation_settles_owned_tasks_before_cleanup() -> None:
    events: list[str] = []

    class FailedWorker(worker_module.GraphitiWorker):
        def __init__(self) -> None:
            self.clients = {}

        async def operation(self, request: dict[str, Any]) -> Any:
            async def pending() -> None:
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("task_settled")

            asyncio.create_task(pending())
            await asyncio.sleep(0)
            raise ValueError("synthetic_sdk_failure")

        async def close(self) -> None:
            events.append("cleanup")

    with pytest.raises(ValueError, match="synthetic_sdk_failure"):
        await FailedWorker().settled_operation({})
    assert events == ["task_settled", "cleanup"]


@pytest.mark.asyncio
async def test_vendor_model_override_fails_before_parent_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_model_call(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("model_override_dispatched")

    monkeypatch.setattr(worker_module, "model_call", unexpected_model_call)
    with pytest.raises(ValueError, match="graphiti_model_override_rejected"):
        await worker_module.ParentLLM(ANSWER_MODEL)._generate_response([], model="unfrozen-model")


@pytest.mark.asyncio
async def test_real_graphiti_ingest_hybrid_search_clock_tenants_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    port = os.getenv("CME_BASELINE_TEST_FALKOR_PORT")
    if port is None:
        pytest.skip("CME_BASELINE_TEST_FALKOR_PORT must name a disposable local FalkorDB service")
    calls: list[tuple[str, dict[str, Any]]] = []
    network: list[tuple[Any, ...]] = []
    original_connect = socket.socket.connect

    def local_connect(sock: socket.socket, address: Any) -> Any:
        assert address[0] in {"127.0.0.1", "::1"}
        assert address[1] == int(port)
        network.append(address)
        return original_connect(sock, address)

    def model_call(kind: str, **payload: Any) -> Any:
        calls.append((kind, payload))
        if kind == "embed":
            return [[1.0, *([0.0] * 383)] for _ in payload["texts"]]
        title = payload["schema"]["title"]
        if title == "ExtractedEntities":
            result = {
                "extracted_entities": [
                    {"name": name, "entity_type_id": 0, "episode_indices": [0]} for name in ("Ada", "Jasmine Tea")
                ]
            }
        elif title == "ExtractedEdges":
            result = {
                "edges": [
                    {
                        "source_entity_name": "Ada",
                        "target_entity_name": "Jasmine Tea",
                        "relation_type": "LIKES",
                        "fact": "Ada likes Jasmine Tea.",
                        "valid_at": "2020-01-01T00:00:00Z",
                        "invalid_at": None,
                        "episode_indices": [0],
                    }
                ]
            }
        elif title == "SummarizedEntities":
            result = {
                "summaries": [
                    {"name": "Ada", "summary": "Ada likes Jasmine Tea."},
                    {"name": "Jasmine Tea", "summary": "Jasmine Tea is liked by Ada."},
                ]
            }
        elif title == "NodeResolutions":
            result = {
                "entity_resolutions": [
                    {"id": index, "name": name, "duplicate_candidate_id": -1}
                    for index, name in enumerate(("Ada", "Jasmine Tea"))
                ]
            }
        elif title == "EdgeDuplicate":
            result = {"duplicate_facts": [], "contradicted_facts": []}
        else:
            raise AssertionError(f"unexpected_actual_sdk_response_schema:{title}")
        return json.dumps(result)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(worker_module, "model_call", model_call)

    def unexpected_default(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unexpected_default_graphiti_client")

    for name in ("OpenAIClient", "OpenAIEmbedder", "OpenAIRerankerClient", "Neo4jDriver"):
        monkeypatch.setattr(graphiti_module, name, unexpected_default)
    worker = worker_module.GraphitiWorker(
        {"settings": {"answer_model": ANSWER_MODEL}, "graph": {"host": "127.0.0.1", "port": int(port)}}
    )
    tenant_a, tenant_b = "cme_" + uuid4().hex * 2, "cme_" + uuid4().hex * 2
    sentinel = "cme_test_foreign_" + uuid4().hex
    clock = "2020-03-04T00:00:00+00:00"
    try:
        result = await worker.settled_operation(
            {
                "tenant": tenant_a,
                "clock": clock,
                "operation": "ingest",
                "turn": {
                    "turn_id": "turn-a",
                    "role": "user",
                    "source_speaker": "Ada",
                    "content": "Ada likes Jasmine Tea.",
                    "occurred_at": "2020-01-01T00:00:00+00:00",
                },
            }
        )
        assert result["episode_id"]
        found = await worker.settled_operation(
            {"tenant": tenant_a, "clock": clock, "operation": "retrieve", "query": "What does Ada like?", "limit": 5}
        )
        assert len(found) == 1
        assert found[0]["content"] == "Ada likes Jasmine Tea."
        assert found[0]["episode_ids"] == [result["episode_id"]]
        other = await worker.settled_operation(
            {"tenant": tenant_b, "clock": clock, "operation": "retrieve", "query": "What does Ada like?", "limit": 5}
        )
        assert other == []
        driver = worker.clients[tenant_a].driver
        # This fixture-owned graph is deliberately outside this worker's map.
        # Cleanup must preserve it while deleting only A and B.
        await driver.client.select_graph(sentinel).query("CREATE (:Fixture {value: 'preserve'})")
        rows, _, _ = await driver.execute_query(
            "MATCH (e:Episodic) RETURN e.created_at AS recorded, e.valid_at AS valid"
        )
        assert rows[0]["recorded"].startswith("2020-03-04")
        assert rows[0]["valid"].startswith("2020-01-01")
        assert network
        embeddings = [payload for kind, payload in calls if kind == "embed"]
        assert any(payload["query"] is True for payload in embeddings)
        assert any(payload["query"] is False for payload in embeddings)
        assert all(len(payload["texts"]) > 0 for payload in embeddings)
        llm_calls = [payload for kind, payload in calls if kind == "llm"]
        assert {payload["schema"]["title"] for payload in llm_calls} >= {"ExtractedEntities", "ExtractedEdges"}
        assert all(payload["effective_max_tokens"] == 2048 for payload in llm_calls)
        assert any(payload["requested_max_tokens"] == 16384 for payload in llm_calls)
        assert any("Ada" in payload["texts"] and payload["query"] is True for payload in embeddings)
        assert any(
            "Ada likes Jasmine Tea." in payload["texts"] and payload["query"] is False for payload in embeddings
        )
        assert any("Ada likes Jasmine Tea." in payload["texts"] and payload["query"] is True for payload in embeddings)
        assert worker.clients[tenant_a].llm_client.model == ANSWER_MODEL
        assert worker.clients[tenant_a].llm_client.small_model == ANSWER_MODEL
        assert worker.clients[tenant_a].llm_client.max_tokens == 2048
    finally:
        clients = list(worker.clients.values())
        await worker.close()
        # A fresh connection verifies graph deletion, not merely object disposal.
        probe = worker_module.FalkorDB(host="127.0.0.1", port=int(port), socket_timeout=3, socket_connect_timeout=3)
        try:
            remaining = await probe.list_graphs()
            assert tenant_a not in remaining and tenant_b not in remaining
            assert sentinel in remaining
        finally:
            if await probe.connection.exists(sentinel):
                await probe.select_graph(sentinel).delete()
            await probe.connection.aclose()
        assert clients


@pytest.mark.asyncio
async def test_actual_sdk_schema_failure_deletes_owned_graphs(monkeypatch: pytest.MonkeyPatch) -> None:
    port = os.getenv("CME_BASELINE_TEST_FALKOR_PORT")
    if port is None:
        pytest.skip("CME_BASELINE_TEST_FALKOR_PORT must name a disposable local FalkorDB service")
    attempted: list[str] = []

    def malformed_response(kind: str, **payload: Any) -> Any:
        attempted.append(kind)
        if kind == "embed":
            return [[1.0, *([0.0] * 383)] for _ in payload["texts"]]
        return "malformed_json"

    monkeypatch.setattr(worker_module, "model_call", malformed_response)
    worker = worker_module.GraphitiWorker(
        {"settings": {"answer_model": ANSWER_MODEL}, "graph": {"host": "127.0.0.1", "port": int(port)}}
    )
    tenant = "cme_" + uuid4().hex * 2
    with pytest.raises(json.JSONDecodeError):
        await worker.settled_operation(
            {
                "tenant": tenant,
                "clock": "2020-03-04T00:00:00+00:00",
                "operation": "ingest",
                "turn": {
                    "turn_id": "failed-turn",
                    "role": "user",
                    "source_speaker": "Ada",
                    "content": "Ada likes Jasmine Tea.",
                    "occurred_at": "2020-01-01T00:00:00+00:00",
                },
            }
        )
    assert "llm" in attempted
    assert worker.clients == {}
    probe = worker_module.FalkorDB(host="127.0.0.1", port=int(port), socket_timeout=3, socket_connect_timeout=3)
    try:
        assert tenant not in await probe.list_graphs()
    finally:
        await probe.connection.aclose()


def test_parent_eof_also_cleans_actual_sdk_graph_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    port = os.getenv("CME_BASELINE_TEST_FALKOR_PORT")
    if port is None:
        pytest.skip("CME_BASELINE_TEST_FALKOR_PORT must name a disposable local FalkorDB service")
    tenant = "cme_" + uuid4().hex * 2
    requests = iter(
        [
            {"settings": {"answer_model": ANSWER_MODEL}, "graph": {"host": "127.0.0.1", "port": int(port)}},
            {
                "tenant": tenant,
                "clock": "2020-03-04T00:00:00+00:00",
                "operation": "retrieve",
                "query": "What does Ada like?",
                "limit": 5,
            },
        ]
    )
    responses: list[dict[str, Any]] = []

    def receive() -> dict[str, Any]:
        try:
            return next(requests)
        except StopIteration:
            raise ValueError("worker_message_limit_or_eof") from None

    def embedding_only(kind: str, **payload: Any) -> Any:
        assert kind == "embed"
        return [[1.0, *([0.0] * 383)] for _ in payload["texts"]]

    monkeypatch.setattr(worker_module, "receive", receive)
    monkeypatch.setattr(worker_module, "send", responses.append)
    monkeypatch.setattr(worker_module, "model_call", embedding_only)

    async def verify() -> None:
        probe = worker_module.FalkorDB(host="127.0.0.1", port=int(port), socket_timeout=3, socket_connect_timeout=3)
        try:
            assert tenant not in await probe.list_graphs()
        finally:
            await probe.connection.aclose()

    # main owns an asyncio.Runner just as it does in its real child process.
    # Isolate that loop from pytest-asyncio's per-test loop policy on Windows.
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="worker_message_limit_or_eof"):
            executor.submit(worker_module.main).result(timeout=30)
        assert responses[0]["type"] == "ready"
        assert responses[1] == {"type": "result", "result": []}
        executor.submit(asyncio.run, verify()).result(timeout=15)
