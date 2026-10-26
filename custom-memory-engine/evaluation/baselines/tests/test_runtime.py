"""Controlled transport and accounting checks, without a provider or model call."""

import importlib
from decimal import Decimal

import pytest

from evaluation.baselines.b0_session import B0SessionAdapter
from evaluation.baselines.contracts import BaselineSettings, Completion, Embeddings, Message
from evaluation.baselines.runtime import RetryableTransportError
from evaluation.baselines.tests.helpers import FakeServices, config
from evaluation.baselines.tests.helpers import settings as helper_settings
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.models import Usage


def settings() -> BaselineSettings:
    return BaselineSettings(
        tokenizer_id="synthetic-character-v1",
        answer_prompt="Use the supplied context.",
        context_window_tokens=4096,
        retrieval_k=3,
        dense_candidates=10,
        lexical_candidates=10,
        input_usd_per_million=Decimal("0.40"),
        output_usd_per_million=Decimal("1.60"),
        max_llm_calls_per_operation=2,
        max_embedding_calls_per_operation=2,
        provider_attempts=1,
        provider_timeout_seconds=5.0,
        vendor_timeout_seconds=30.0,
    )


class Tokenizer:
    identity = "synthetic-character-v1"

    def encode(self, text):
        return [ord(c) for c in text]

    def decode(self, tokens):
        return "".join(chr(t) for t in tokens)

    def count_messages(self, messages):
        return 3 + sum(3 + len(m.role) + len(m.content) for m in messages)


class Embedder:
    def chunks(self, text):
        return (text,)

    def token_count(self, texts, *, query=False):
        return len(texts)

    def embed(self, texts, *, query=False):
        return Embeddings(
            vectors=tuple((1.0,) + (0.0,) * 383 for _ in texts),
            usage=Usage(input_tokens=0, output_tokens=0, embedding_tokens=len(texts), cost_usd=Decimal(0)),
        )


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def request(self, payload):
        self.requests.append(payload)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def response(model="gpt-4.1-mini-2025-04-14", usage=True):
    value = {"model": model, "choices": [{"message": {"content": "fictional answer"}, "finish_reason": "stop"}]}
    if usage:
        value["usage"] = {"prompt_tokens": 8, "completion_tokens": 2}
    return value


def runtime(transport, cfg=None):
    module = importlib.import_module("evaluation.baselines.runtime")
    return module.MeteredServices(cfg or settings(), Tokenizer(), Embedder(), transport)


def maximum():
    return Usage(input_tokens=10_000, output_tokens=4096, embedding_tokens=100, cost_usd=Decimal("1"))


def test_provider_request_freezes_model_decoding_and_reports_actual_usage():
    transport = Transport([response()])
    services = runtime(transport)
    with services.scope(maximum()) as scope:
        result = services.complete((Message(role="user", content="test"),))
    assert isinstance(result, Completion) and result.text == "fictional answer"
    assert scope.usage == Usage(input_tokens=8, output_tokens=2, embedding_tokens=0, cost_usd=Decimal("0.0000064"))
    request = transport.requests[0]
    assert request["model"] == "gpt-4.1-mini-2025-04-14"
    assert request["temperature"] == 0 and request["top_p"] == 1
    assert request["max_completion_tokens"] == 2048
    assert request["store"] is False and request["stream"] is False
    assert "seed" not in request


def test_operation_call_limit_blocks_before_extra_request():
    transport = Transport([response(), response(), response()])
    services = runtime(transport)
    with services.scope(maximum()) as scope:
        services.complete((Message(role="user", content="one"),))
        services.complete((Message(role="user", content="two"),))
        with pytest.raises(AccountedOperationError, match="operation_call_limit"):
            services.complete((Message(role="user", content="three"),))
    assert len(transport.requests) == 2 and scope.usage.input_tokens == 16


@pytest.mark.parametrize("bad", [response(model="floating"), response(usage=False), TimeoutError("private detail")])
def test_unknown_usage_or_model_mismatch_is_not_reported_as_zero(bad):
    services = runtime(Transport([bad]))
    with services.scope(maximum()) as scope:
        with pytest.raises(AccountedOperationError) as failure:
            services.complete((Message(role="user", content="test"),))
    assert scope.usage.tokens > 0 and failure.value.usage == scope.usage
    assert "private detail" not in str(failure.value)


def test_reservation_denial_makes_no_request():
    transport = Transport([response()])
    services = runtime(transport)
    with services.scope(Usage.zero()):
        with pytest.raises(AccountedOperationError):
            services.complete((Message(role="user", content="test"),))
    assert transport.requests == []


def test_scope_required_and_embedding_accounted():
    services = runtime(Transport([]))
    with pytest.raises(RuntimeError, match="operation_scope_required"):
        services.embed(("x",))
    with services.scope(maximum()) as scope:
        result = services.embed(("x", "y"), query=True)
    assert len(result.vectors) == 2 and scope.usage.embedding_tokens == 2


def test_nested_scope_rejected_without_losing_outer_accounting():
    services = runtime(Transport([response()]))
    with services.scope(maximum()) as scope:
        services.complete((Message(role="user", content="x"),))
        with pytest.raises(RuntimeError, match="nested_operation_scope"):
            with services.scope(maximum()):
                pass
    assert scope.usage.input_tokens == 8


def test_schema_defaults_are_normalized_only_for_provider_payload():
    schema = {
        "type": "object",
        "properties": {"entities": {"type": "array", "items": {"$ref": "#/$defs/Entity"}}},
        "$defs": {
            "Entity": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "valid_at": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                },
                "required": ["name"],
            }
        },
    }
    good = response()
    good["choices"][0]["message"]["content"] = '{"entities": []}'
    transport = Transport([good])
    services = runtime(transport)
    with services.scope(maximum()):
        services.complete((Message(role="user", content="Return JSON"),), schema=schema)
    sent = transport.requests[0]["response_format"]["json_schema"]["schema"]
    assert sent["required"] == ["entities"] and sent["additionalProperties"] is False
    assert sent["$defs"]["Entity"]["required"] == ["name", "valid_at"]
    assert sent["$defs"]["Entity"]["additionalProperties"] is False
    assert "default" not in sent["$defs"]["Entity"]["properties"]["valid_at"]
    assert schema["$defs"]["Entity"]["properties"]["valid_at"]["default"] is None


def test_failed_retry_bound_and_successful_usage_are_both_retained(monkeypatch):
    module = importlib.import_module("evaluation.baselines.runtime")
    times = iter((1.25, 1.5, 2.0, 2.75))
    monkeypatch.setattr(module, "perf_counter", lambda: next(times), raising=False)
    transport = Transport([RetryableTransportError(), response()])
    selected = settings().model_copy(update={"provider_attempts": 2})
    services = runtime(transport, selected)
    messages = (Message(role="user", content="test"),)
    with services.scope(maximum()) as scope:
        result = services.complete(messages)
    bound = Tokenizer().count_messages(messages) + selected.request_overhead_tokens
    assert scope.usage.input_tokens == bound + 8
    assert scope.usage.output_tokens == 2050
    assert scope.usage.cost_usd == (Decimal(bound + 8) * Decimal("0.4") + Decimal(2050) * Decimal("1.6")) / 1000000
    assert result.usage == scope.usage
    assert services.operation_metrics()["llm_attempts"] == 2
    assert services.operation_metrics()["llm_failed"] == 1
    assert services.operation_metrics()["llm_wall_ms"] == 1000
    calls = services.operation_metrics()["llm_calls"]
    assert [call["latency_ms"] for call in calls] == [250, 750]
    assert [call["usage_basis"] for call in calls] == ["conservative_bound", "provider_reported"]
    assert [call["success"] for call in calls] == [False, True]


def test_embedding_budget_denial_precedes_inference():
    class CountingEmbedder(Embedder):
        calls = 0

        def embed(self, texts, *, query=False):
            self.calls += 1
            return super().embed(texts, query=query)

    module = importlib.import_module("evaluation.baselines.runtime")
    embedder = CountingEmbedder()
    services = module.MeteredServices(settings(), Tokenizer(), embedder, Transport([]))
    with services.scope(Usage.zero()) as scope:
        with pytest.raises(AccountedOperationError):
            services.embed(("test",))
    assert embedder.calls == 0 and scope.usage == Usage.zero()


@pytest.mark.parametrize(
    "field,value",
    [
        ("answer_model", "floating-unfrozen-model"),
        ("embedding_revision", "0" * 40),
        ("rrf_constant", 99),
        ("max_output_tokens", 5000),
    ],
)
def test_direct_adapter_revalidates_copied_frozen_settings(field, value):
    selected = helper_settings().model_copy(update={field: value})
    with pytest.raises(ValueError):
        B0SessionAdapter(config("B0", selected), selected, FakeServices(selected))


def test_model_runtime_revalidates_copied_settings_before_dispatch():
    selected = settings().model_copy(update={"answer_model": "floating-unfrozen-model"})
    with pytest.raises(ValueError):
        runtime(Transport([response()]), selected)


def test_embedding_underreported_usage_fails_and_retains_exact_planned_tokens():
    class Underreporting(Embedder):
        def token_count(self, texts, *, query=False):
            return 17

        def embed(self, texts, *, query=False):
            return Embeddings(vectors=((1.0,) + (0.0,) * 383,), usage=Usage.zero())

    module = importlib.import_module("evaluation.baselines.runtime")
    services = module.MeteredServices(settings(), Tokenizer(), Underreporting(), Transport([]))
    with services.scope(maximum()) as scope:
        with pytest.raises(AccountedOperationError):
            services.embed(("fictional",))
    assert scope.usage.embedding_tokens == 17
