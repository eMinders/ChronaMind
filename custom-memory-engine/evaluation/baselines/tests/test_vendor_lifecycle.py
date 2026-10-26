"""Real parent IPC + Graphiti/Falkor lifecycle with only model I/O scripted."""

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import evaluation.baselines.vendor as vendor_module
from evaluation.baselines.contracts import ANSWER_MODEL, Embeddings
from evaluation.baselines.runtime import MeteredServices
from evaluation.baselines.tests.helpers import CharacterTokenizer, config, settings, turn
from evaluation.baselines.vendor import B4GraphitiAdapter
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.models import QuestionRecord, Usage

redis = pytest.importorskip("redis", reason="install the locked baselines extra for vendor lifecycle integration")
pytestmark = pytest.mark.skipif(
    find_spec("graphiti_core") is None, reason="install the locked baselines extra for vendor lifecycle integration"
)
CLOCK = datetime(2020, 3, 4, tzinfo=UTC)


class ScriptedEmbedder:
    def chunks(self, text: str) -> tuple[str, ...]:
        return (text,)

    def token_count(self, texts: Sequence[str], *, query: bool = False) -> int:
        return sum(max(1, len(text)) for text in texts)

    def embed(self, texts: Sequence[str], *, query: bool = False) -> Embeddings:
        return Embeddings(
            vectors=tuple((1.0, *([0.0] * 383)) for _ in texts),
            usage=Usage(
                input_tokens=0,
                output_tokens=0,
                embedding_tokens=self.token_count(texts, query=query),
                cost_usd=Decimal(0),
            ),
        )


class ScriptedTransport:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail_at: int | None = None

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        if len(self.requests) == self.fail_at:
            raise TimeoutError("private_provider_failure_should_not_escape")
        response_format = payload.get("response_format")
        if response_format is None:
            content = "Jasmine Tea."
        else:
            title = response_format["json_schema"]["schema"]["title"]
            if title == "ExtractedEntities":
                response = {
                    "extracted_entities": [
                        {"name": name, "entity_type_id": 0, "episode_indices": [0]} for name in ("Ada", "Jasmine Tea")
                    ]
                }
            elif title == "ExtractedEdges":
                response = {
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
            else:
                raise AssertionError(f"unexpected_actual_sdk_schema:{title}")
            content = json.dumps(response)
        return {
            "model": ANSWER_MODEL,
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        }


def create_adapter() -> tuple[B4GraphitiAdapter, ScriptedTransport, dict[str, Any]]:
    port = os.getenv("CME_BASELINE_TEST_FALKOR_PORT")
    if port is None:
        pytest.skip("CME_BASELINE_TEST_FALKOR_PORT must name a disposable local FalkorDB service")
    selected = settings(
        context_window_tokens=1_047_576, vendor_timeout_seconds=90.0, max_embedding_calls_per_operation=30
    )
    transport = ScriptedTransport()
    services = MeteredServices(selected, CharacterTokenizer(), ScriptedEmbedder(), transport)
    graph: dict[str, Any] = {"host": "127.0.0.1", "port": int(port)}
    adapter = B4GraphitiAdapter(config("B4", selected), selected, services, python=Path(sys.executable), graph=graph)
    adapter.reset_user("user")
    adapter.set_clock(CLOCK)
    return adapter, transport, graph


def test_parent_graphiti_ipc_metering_evidence_and_prepared_answer() -> None:
    adapter, transport, _ = create_adapter()
    try:
        receipt = adapter.ingest_turn(turn("source-turn", "Ada likes Jasmine Tea.", speaker="Ada"))
        assert receipt.usage.input_tokens == 22
        assert receipt.usage.output_tokens == 6
        assert receipt.usage.embedding_tokens > 0
        episodes = [event for event in adapter.drain_adapter_events() if event["event"] == "vendor_episode"]
        assert len(episodes) == 1 and episodes[0]["success"] is True
        metrics = episodes[0]["model_calls"]
        assert isinstance(metrics, dict)
        assert metrics["llm_attempts"] == metrics["llm_succeeded"] == 2
        assert metrics["llm_failed"] == 0
        assert metrics["llm_wall_ms"] > 0
        assert len(metrics["llm_calls"]) == 2
        assert all(
            call["latency_ms"] > 0 and call["usage_basis"] == "provider_reported" for call in metrics["llm_calls"]
        )
        assert metrics["usage"] == receipt.usage.model_dump(mode="json")
        probe = QuestionRecord(question_id="probe", user_id="user", question="What does Ada like?")
        adapter.prepare_question(probe)
        retrieval = adapter.retrieve("user", probe.question, "current", CLOCK, adapter.config.B_mem)
        assert len(retrieval.items) == 1
        assert retrieval.items[0].evidence_turn_ids == ("source-turn",)
        assert retrieval.unavailable_metrics == ()
        assert retrieval.rendered_context == "Ada likes Jasmine Tea."
        request_count = len(transport.requests)
        answer = adapter.answer("user", probe, CLOCK)
        assert answer.answer == "Jasmine Tea."
        assert len(transport.requests) == request_count + 1
        assert "Ada likes Jasmine Tea." in transport.requests[-1]["messages"][-1]["content"]
        assert all(
            request["model"] == ANSWER_MODEL
            and request["temperature"] == 0
            and request["top_p"] == 1
            and request["max_completion_tokens"] == 2048
            for request in transport.requests
        )
        assert adapter.adapter_metadata()["D21"] == "unmeasured"
    finally:
        adapter.close()


@pytest.mark.parametrize("failure_stage", ["helper", "temporary", "temporary_then_helper"])
def test_forced_worker_kill_recovers_only_parent_tracked_namespaces(
    monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    adapter, _, graph = create_adapter()
    tenant = adapter._tenant("user")
    sentinel = "cme_lifecycle_foreign_" + uuid4().hex
    with redis.Redis(host="127.0.0.1", port=graph["port"], socket_timeout=3, socket_connect_timeout=3) as connection:
        try:
            adapter.ingest_turn(turn("source-turn", "Ada likes Jasmine Tea.", speaker="Ada"))
            assert connection.exists(tenant)
            connection.execute_command("GRAPH.QUERY", sentinel, "CREATE (:Fixture {value: 'preserve'})")
            process = adapter._worker.process
            assert process is not None
            process.kill()
            process.wait(timeout=5)
            actual_run = subprocess.run
            injected_failure = False
            injected_temporary_failure = False

            if failure_stage != "helper":
                temporary = adapter._worker._temporary
                assert temporary is not None
                actual_cleanup = temporary.cleanup

                def fail_temporary_cleanup_once() -> None:
                    nonlocal injected_temporary_failure
                    if not injected_temporary_failure:
                        injected_temporary_failure = True
                        raise PermissionError("fixture_transient_file_lock")
                    actual_cleanup()

                monkeypatch.setattr(temporary, "cleanup", fail_temporary_cleanup_once)

            def fail_cleanup_once(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
                nonlocal injected_failure
                if (
                    failure_stage != "temporary"
                    and not injected_failure
                    and any("evaluation.baselines.graph_cleanup" in arg for arg in args)
                ):
                    injected_failure = True
                    return subprocess.CompletedProcess(args, 1, stdout=b'{"ok":false}')
                return actual_run(args, **kwargs)

            monkeypatch.setattr(vendor_module.subprocess, "run", fail_cleanup_once)
            if failure_stage == "helper":
                with pytest.raises(RuntimeError, match="owned_graph_cleanup_failed"):
                    adapter.close()
                assert injected_failure
            else:
                with pytest.raises(PermissionError, match="fixture_transient_file_lock"):
                    adapter.close()
                assert injected_temporary_failure and not injected_failure
            assert connection.exists(tenant)
            if failure_stage == "temporary_then_helper":
                with pytest.raises(RuntimeError, match="owned_graph_cleanup_failed"):
                    adapter.close()
                assert injected_failure and connection.exists(tenant)
            # Recovery retains the parent-owned scope even with no live child.
            adapter.close()
            assert not connection.exists(tenant)
            assert connection.exists(sentinel)
        finally:
            adapter.close()
            for name in (tenant, sentinel):
                if connection.exists(name):
                    connection.execute_command("GRAPH.DELETE", name)


def test_parent_sdk_failure_preserves_every_attempt_charge_and_cleans_graph() -> None:
    adapter, transport, graph = create_adapter()
    transport.fail_at = 2
    tenant = adapter._tenant("user")
    try:
        with pytest.raises(AccountedOperationError) as failure:
            adapter.ingest_turn(turn("failed-turn", "Ada likes Jasmine Tea.", speaker="Ada"))
        assert failure.value.usage.input_tokens > 11
        assert failure.value.usage.output_tokens > 3
        assert len(transport.requests) == 2
        episodes = [event for event in adapter.drain_adapter_events() if event["event"] == "vendor_episode"]
        assert len(episodes) == 1 and episodes[0]["success"] is False
        metrics = episodes[0]["model_calls"]
        assert isinstance(metrics, dict)
        assert metrics["llm_attempts"] == 2 and metrics["llm_succeeded"] == 1 and metrics["llm_failed"] == 1
        assert metrics["usage"] == failure.value.usage.model_dump(mode="json")
        assert metrics["llm_calls"][1]["usage_basis"] == "conservative_bound"
        assert metrics["llm_calls"][1]["success"] is False
        assert "private_provider_failure" not in json.dumps(episodes)
        assert "private_provider_failure" not in str(failure.value)
        with redis.Redis(
            host="127.0.0.1", port=graph["port"], socket_timeout=3, socket_connect_timeout=3
        ) as connection:
            assert not connection.exists(tenant)
        # Poisoned state cannot continue after losing the failed worker's state.
        with pytest.raises(AccountedOperationError):
            adapter.ingest_turn(turn("retry-turn", "No hidden retry."))
        assert len(transport.requests) == 2
    finally:
        adapter.close()
