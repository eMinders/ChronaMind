"""Actual isolated Mem0/Qdrant SDK, with fakes only at model/transport boundaries."""

import importlib.metadata
import json
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evaluation.baselines.contracts import EVIDENCE_METRICS, Embeddings
from evaluation.baselines.runtime import MeteredServices
from evaluation.baselines.tests.helpers import CharacterTokenizer, config, settings, turn, vector
from evaluation.baselines.vendor import B3Mem0Adapter
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.models import QuestionRecord, Usage

CLOCK = datetime(2020, 1, 2, tzinfo=UTC)


def _guard_program(path: Path) -> str:
    """Instrument genuine child network/provider boundaries before SDK imports."""
    return textwrap.dedent(
        """
        import hashlib, importlib.metadata, importlib.util, json, pathlib, socket, urllib.request
        _observed_path = pathlib.Path(__OBS_PATH__)
        _observed = {"network_attempts": 0, "provider_constructors": 0, "telemetry_constructors": 0}
        _package = pathlib.Path(importlib.util.find_spec("mem0").origin).parent
        _observed["mem0_source_sha256"] = hashlib.sha256((_package / "memory/main.py").read_bytes()).hexdigest()
        _observed["mem0_version"] = importlib.metadata.version("mem0ai")
        _observed_path.write_text(json.dumps(_observed))
        def _deny(_kind):
            def _blocked(*args, **kwargs):
                _observed[_kind] += 1
                _observed_path.write_text(json.dumps(_observed))
                raise AssertionError("unexpected_" + _kind)
            return _blocked
        socket.socket.connect = _deny("network_attempts")
        socket.socket.connect_ex = _deny("network_attempts")
        socket.socket.sendto = _deny("network_attempts")
        socket.create_connection = _deny("network_attempts")
        urllib.request.urlopen = _deny("network_attempts")
        import openai, posthog, httpx, requests
        for _name in ("OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI"):
            setattr(openai, _name, _deny("provider_constructors"))
        posthog.Posthog = _deny("telemetry_constructors")
        httpx.Client.send = _deny("network_attempts")
        httpx.AsyncClient.send = _deny("network_attempts")
        requests.Session.request = _deny("network_attempts")
        """
    ).replace("__OBS_PATH__", repr(str(path)))


@pytest.fixture
def guarded_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A baseline dependency absence is an explicit optional-integration skip.
    for package, pin in (("mem0ai", "2.2.1"), ("qdrant-client", "1.20.0")):
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pytest.skip(f"optional real SDK integration requires {package}=={pin}")
        assert installed == pin
    observations = tmp_path / "child-boundary-observations.json"
    original = subprocess.Popen
    processes: list[subprocess.Popen[Any]] = []

    def instrumented(command, *args, **kwargs):
        revised = list(command)
        assert revised[2] == "-c"
        revised[3] = _guard_program(observations) + "\n" + revised[3]
        child = original(revised, *args, **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", instrumented)
    yield observations
    assert processes, "the real worker was never started"
    assert all(child.poll() is not None for child in processes), "owned worker leaked"
    observed = json.loads(observations.read_text())
    assert observed["mem0_version"] == "2.2.1"
    assert observed["mem0_source_sha256"] == "f329ca4a17af1fbefb32ac10d4cc7ae7ddbdb7435a87eb74c30915cff98b8b7c"
    assert observed["network_attempts"] == observed["provider_constructors"] == observed["telemetry_constructors"] == 0


class FictionalEmbeddings:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def chunks(self, text: str) -> tuple[str, ...]:
        return (text,)

    def token_count(self, texts: Sequence[str], *, query: bool = False) -> int:
        return sum(len(text) + 2 + (9 if query else 0) for text in texts)

    def embed(self, texts: Sequence[str], *, query: bool = False) -> Embeddings:
        self.calls.append((tuple(texts), query))
        vectors = []
        for text in texts:
            if "Orin" in text:
                vectors.append((0.0, 0.0, 1.0, *(0.0 for _ in range(381))))
            elif "brass compass" in text and "moon tea" not in text:
                vectors.append(vector(0.8, 0.6))
            else:
                vectors.append(vector(1.0))
        return Embeddings(
            vectors=tuple(vectors),
            usage=Usage(
                input_tokens=0,
                output_tokens=0,
                embedding_tokens=self.token_count(texts, query=query),
                cost_usd=Decimal(0),
            ),
        )


class FictionalTransport:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = iter(responses)
        self.requests: list[dict[str, Any]] = []

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        assert payload["model"] == "gpt-4.1-mini-2025-04-14"
        assert payload["temperature"] == 0 and payload["top_p"] == 1
        assert payload["max_completion_tokens"] == 2048
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return {
            "model": payload["model"],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            "choices": [{"message": {"content": value}, "finish_reason": "stop"}],
        }


def _adapter(responses: list[str | Exception]):
    selected = settings(
        context_window_tokens=50_000, vendor_timeout_seconds=90.0, max_embedding_calls_per_operation=30
    )
    embedder = FictionalEmbeddings()
    transport = FictionalTransport(responses)
    services = MeteredServices(selected, CharacterTokenizer(), embedder, transport)
    adapter = B3Mem0Adapter(config("B3", selected), selected, services, python=Path(sys.executable))
    return adapter, services, embedder, transport


def _facts(*facts: str) -> str:
    return json.dumps({"memory": [{"text": fact} for fact in facts]})


def _retrieve(adapter: B3Mem0Adapter, user: str, text: str):
    probe = QuestionRecord(question_id="question", user_id=user, question=text)
    adapter.prepare_question(probe)
    return probe, adapter.retrieve(user, text, "current", CLOCK, adapter.config.B_mem)


def test_real_mem0_worker_native_extraction_rank_tenant_scope_and_prepared_answer(guarded_worker: Path) -> None:
    adapter, services, embeddings, transport = _adapter(
        [
            _facts("Captain Nira prefers moon tea.", "Captain Nira owns a brass compass."),
            _facts("Orin prefers sun coffee."),
            "A fictional answer based on the supplied context.",
        ]
    )
    try:
        adapter.reset_user("nira")
        adapter.reset_user("orin")
        adapter.set_clock(CLOCK)
        first = adapter.ingest_turn(
            turn("nira1", "Captain Nira prefers moon tea and owns a brass compass.", user="nira")
        )
        second = adapter.ingest_turn(turn("orin1", "Orin prefers sun coffee.", user="orin"))
        assert first.usage.input_tokens == second.usage.input_tokens == 11
        assert first.usage.output_tokens == second.usage.output_tokens == 3
        assert first.usage.embedding_tokens > 0
        assert "sole operation is ADD" in transport.requests[0]["messages"][0]["content"]
        assert transport.requests[0].get("response_format") == {"type": "json_object"}
        assert transport.requests[1].get("response_format") == {"type": "json_object"}
        assert any(query for _, query in embeddings.calls) and any(not query for _, query in embeddings.calls)
        probe, result = _retrieve(adapter, "nira", "What does Captain Nira prefer?")
        assert [item.content for item in result.items] == [
            "Captain Nira prefers moon tea.",
            "Captain Nira owns a brass compass.",
        ]
        assert all(not item.evidence_turn_ids for item in result.items)
        assert result.unavailable_metrics == EVIDENCE_METRICS
        assert result.unavailable_metrics_reason == "vendor_evidence_provenance_unavailable"
        before_answer = len(embeddings.calls)
        answer = adapter.answer("nira", probe, CLOCK)
        assert answer.answer == "A fictional answer based on the supplied context."
        assert len(embeddings.calls) == before_answer
        assert result.rendered_context in transport.requests[-1]["messages"][-1]["content"]
        assert "response_format" not in transport.requests[-1]
        _, orin = _retrieve(adapter, "orin", "What does Orin prefer?")
        assert [item.content for item in orin.items] == ["Orin prefers sun coffee."]
        requests = len(transport.requests)
        deletion = adapter.delete("nira", "all", "nira")
        assert deletion.supported is False and deletion.usage == Usage.zero()
        assert len(transport.requests) == requests
        adapter.reset_user("nira")
        _, reset = _retrieve(adapter, "nira", "What does Captain Nira prefer?")
        assert reset.items == ()  # Old worker storage is outside the new replay generation.
        adapter.set_clock(CLOCK + timedelta(seconds=1))
        with pytest.raises(ValueError, match="prepared_answer_context_mismatch"):
            adapter.answer("nira", probe, CLOCK)
        assert len(transport.requests) == requests
        events = adapter.drain_adapter_events()
        episodes = [event for event in events if event["event"] == "vendor_episode"]
        assert len(episodes) == 2 and all(event["success"] for event in episodes)
        for event in episodes:
            metrics = event["model_calls"]
            assert isinstance(metrics, dict) and metrics["llm_attempts"] == 1
        assert services.operation_metrics()["llm_attempts"] == 0
    finally:
        adapter.close()


def test_real_mem0_worker_failure_keeps_attempt_usage_and_poisoned_child_cannot_dispatch(guarded_worker: Path) -> None:
    adapter, services, embeddings, transport = _adapter([TimeoutError("private provider detail")])
    try:
        adapter.reset_user("nira")
        adapter.set_clock(CLOCK)
        with pytest.raises(AccountedOperationError) as failure:
            adapter.ingest_turn(turn("failure", "Captain Nira prefers moon tea.", user="nira"))
        assert failure.value.usage.input_tokens > 0
        assert failure.value.usage.output_tokens == 2048
        assert failure.value.usage.embedding_tokens > 0
        assert failure.value.usage.cost_usd > 0
        assert "private provider detail" not in str(failure.value)
        assert len(transport.requests) == 1 and len(embeddings.calls) == 1
        assert services.operation_metrics()["llm_failed"] == 1
        assert adapter._worker.process is None and adapter._worker._temporary is None
        with pytest.raises(AccountedOperationError):
            adapter.ingest_turn(turn("later", "A later fictional statement.", user="nira"))
        assert len(transport.requests) == 1 and len(embeddings.calls) == 1
    finally:
        adapter.close()


def test_real_mem0_rejects_invalid_replay_turns_before_vendor_dispatch(guarded_worker: Path) -> None:
    adapter, _, _, transport = _adapter([_facts("Captain Nira prefers moon tea.")])
    try:
        adapter.reset_user("nira")
        adapter.set_clock(CLOCK)
        value = turn("same", "Captain Nira prefers moon tea.", user="nira")
        adapter.ingest_turn(value)
        with pytest.raises(ValueError, match="duplicate_baseline_turn"):
            adapter.ingest_turn(value)
        future = value.model_copy(update={"turn_id": "future", "occurred_at": CLOCK + timedelta(days=1)})
        with pytest.raises(ValueError, match="turn_exceeds_replay_clock"):
            adapter.ingest_turn(future)
        assert len(transport.requests) == 1
    finally:
        adapter.close()


def test_real_mem0_native_prompt_preserves_source_date_and_injected_replay_date(guarded_worker: Path) -> None:
    adapter, _, _, transport = _adapter([_facts("Captain Nira prefers moon tea.")])
    try:
        adapter.reset_user("nira")
        adapter.set_clock(CLOCK)
        adapter.ingest_turn(turn("dated", "Yesterday Captain Nira preferred moon tea.", user="nira"))
        prompt = transport.requests[0]["messages"][-1]["content"]
        assert "## Observation Date\n2020-01-01" in prompt
        assert "## Current Date\n2020-01-02" in prompt
        assert "source time: 2020-01-01T00:00:00Z" in prompt
    finally:
        adapter.close()
