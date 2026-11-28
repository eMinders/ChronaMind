"""Bounded model transport with explicit pins, no credential or provider defaults."""

import http.client
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from time import perf_counter
from typing import Protocol
from urllib.parse import urlsplit

from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from evaluation.baselines.contracts import (
    BaselineSettings,
    Completion,
    Embeddings,
    Message,
    Tokenizer,
)
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.models import Usage


class CompletionTransport(Protocol):
    def request(self, payload: dict[str, object]) -> dict[str, object]: ...


class EmbeddingRuntime(Protocol):
    def chunks(self, text: str) -> tuple[str, ...]: ...
    def token_count(self, texts: Sequence[str], *, query: bool = False) -> int: ...
    def embed(self, texts: Sequence[str], *, query: bool = False) -> Embeddings: ...


class RetryableTransportError(Exception):
    """A response with unknown usage; its conservative reservation remains charged."""


def strict_schema(schema: dict[str, object]) -> dict[str, object]:
    """Normalize Pydantic schemas to the provider's strict object requirements.

    Keep the vendor's schema untouched for its own response validation. A free
    form dictionary cannot be narrowed to an empty strict object faithfully.
    """
    result = {key: value for key, value in schema.items() if key != "default"}
    for key in ("$defs", "definitions", "properties"):
        mapping = result.get(key)
        if isinstance(mapping, dict):
            result[key] = {name: strict_schema(value) for name, value in mapping.items()}
    for key in ("items", "additionalProperties"):
        child = result.get(key)
        if isinstance(child, dict):
            result[key] = strict_schema(child)
    for key in ("anyOf", "oneOf", "allOf"):
        alternatives = result.get(key)
        if isinstance(alternatives, list):
            result[key] = [strict_schema(item) for item in alternatives]
    if result.get("type") == "object":
        properties = result.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("structured_object_properties_required")
        if not properties and result.get("additionalProperties") not in (None, False):
            raise ValueError("free_form_structured_object_unsupported")
        result["properties"] = properties
        result["required"] = list(properties)
        result["additionalProperties"] = False
    return result


class OpenAITransport:
    """Real HTTPS API transport; explicit key, fixed origin, no proxy/env/SDK retries.

    A loopback HTTP origin is available only through the explicit test opt-in.
    Connection errors and raw response bodies never enter persistent diagnostics.
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float,
        endpoint: str = "https://api.openai.com/v1/chat/completions",
        allow_loopback_test_endpoint: bool = False,
    ) -> None:
        parsed = urlsplit(endpoint)
        local = (
            allow_loopback_test_endpoint
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        official = (
            parsed.scheme == "https"
            and parsed.hostname == "api.openai.com"
            and parsed.port in (None, 443)
            and parsed.path == "/v1/chat/completions"
        )
        if not (local or official) or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("unsupported_provider_endpoint")
        if not api_key or any(char in api_key for char in "\r\n") or timeout <= 0:
            raise ValueError("explicit_provider_key_and_timeout_required")
        self._key = api_key
        self._url = parsed
        assert parsed.hostname is not None
        self._host = parsed.hostname
        self._timeout = timeout

    def request(self, payload: dict[str, object]) -> dict[str, object]:
        connection_type = http.client.HTTPSConnection if self._url.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self._host, self._url.port, timeout=self._timeout)
        try:
            connection.request(
                "POST",
                self._url.path,
                body=json.dumps(payload, allow_nan=False).encode(),
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read(8 * 1024 * 1024 + 1)
            if response.status in (408, 409, 429) or response.status >= 500:
                raise RetryableTransportError("provider_retryable_response")
            if response.status != 200 or len(body) > 8 * 1024 * 1024:
                raise ValueError("provider_response_rejected")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError("provider_response_object_required")
            return result
        except (OSError, http.client.HTTPException):
            raise RetryableTransportError("provider_transport_failed") from None
        finally:
            connection.close()


def _within(value: Usage, maximum: Usage) -> bool:
    return (
        value.input_tokens <= maximum.input_tokens
        and value.output_tokens <= maximum.output_tokens
        and value.embedding_tokens <= maximum.embedding_tokens
        and value.cost_usd <= maximum.cost_usd
    )


@dataclass
class Meter:
    maximum: Usage
    usage: Usage = field(default_factory=Usage.zero)
    llm_calls: int = 0
    embedding_calls: int = 0
    successful_llm_calls: int = 0
    llm_calls_detail: list[dict[str, object]] = field(default_factory=list)
    llm_wall_ms: float = 0.0

    def reserve(self, bound: Usage) -> None:
        if not _within(self.usage.plus(bound), self.maximum):
            raise AccountedOperationError("operation_usage_limit", self.usage)

    def record(self, usage: Usage) -> None:
        self.usage = self.usage.plus(usage)
        if not _within(self.usage, self.maximum):
            raise AccountedOperationError("operation_usage_limit", self.usage)


class MeteredServices:
    """Shared real model boundary; every attempted subcall stays within its outer reservation."""

    def __init__(
        self,
        settings: BaselineSettings,
        tokenizer: Tokenizer,
        embedder: EmbeddingRuntime,
        transport: CompletionTransport,
    ) -> None:
        settings = BaselineSettings.model_validate(settings.model_dump())
        if tokenizer.identity != settings.tokenizer_id:
            raise ValueError("tokenizer_identity_mismatch")
        self.settings = settings
        self.tokenizer = tokenizer
        self._embedder = embedder
        self._transport = transport
        self._meter: Meter | None = None
        self._last_metrics: dict[str, object] = {}

    @contextmanager
    def scope(self, maximum: Usage) -> Iterator[Meter]:
        if self._meter is not None:
            raise RuntimeError("nested_operation_scope")
        meter = Meter(maximum=maximum)
        self._meter = meter
        try:
            yield meter
        finally:
            self._last_metrics = {
                "llm_attempts": meter.llm_calls,
                "llm_succeeded": meter.successful_llm_calls,
                "llm_failed": meter.llm_calls - meter.successful_llm_calls,
                "llm_wall_ms": meter.llm_wall_ms,
                "llm_calls": deepcopy(meter.llm_calls_detail),
                "embedding_calls": meter.embedding_calls,
                "usage": meter.usage.model_dump(mode="json"),
            }
            self._meter = None

    def operation_metrics(self) -> dict[str, object]:
        """Content-free instrumentation of the last fully settled operation."""
        return deepcopy(self._last_metrics)

    def _active(self) -> Meter:
        if self._meter is None:
            raise RuntimeError("operation_scope_required")
        return self._meter

    def chunks(self, text: str) -> tuple[str, ...]:
        return self._embedder.chunks(text)

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * self.settings.input_usd_per_million
            + Decimal(output_tokens) * self.settings.output_usd_per_million
        ) / Decimal(1_000_000)

    def _attempt(
        self, messages: Sequence[Message], schema: dict[str, object] | None, json_object: bool = False
    ) -> Completion:
        meter = self._active()
        if meter.llm_calls >= self.settings.max_llm_calls_per_operation:
            raise AccountedOperationError("operation_call_limit", meter.usage)
        # Text tokenization plus conservative framing/schema allowance. Provider
        # usage is authoritative; any unexpected excess is persisted and aborts.
        input_bound = self.tokenizer.count_messages(messages) + self.settings.request_overhead_tokens
        if schema is not None:
            schema = strict_schema(schema)
            input_bound += len(self.tokenizer.encode(json.dumps(schema, sort_keys=True))) + 256
        elif json_object:
            input_bound += 32
        if input_bound + self.settings.max_output_tokens > self.settings.context_window_tokens:
            raise AccountedOperationError("model_context_window_exceeded", meter.usage)
        bound = Usage(
            input_tokens=input_bound,
            output_tokens=self.settings.max_output_tokens,
            embedding_tokens=0,
            cost_usd=self._cost(input_bound, self.settings.max_output_tokens),
        )
        meter.reserve(bound)
        meter.llm_calls += 1
        payload: dict[str, object] = {
            "model": self.settings.answer_model,
            "messages": [message.model_dump() for message in messages],
            "temperature": 0,
            "top_p": self.settings.top_p,
            "max_completion_tokens": self.settings.max_output_tokens,
            "n": 1,
            "stream": False,
            "store": False,
        }
        if self.settings.send_seed:
            payload["seed"] = self.settings.seed
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "baseline_output", "strict": True, "schema": schema},
            }
        elif json_object:
            payload["response_format"] = {"type": "json_object"}
        call: dict[str, object] = {
            "attempt": meter.llm_calls,
            "latency_ms": 0.0,
            "usage": bound.model_dump(mode="json"),
            "usage_basis": "conservative_bound",
            "success": False,
        }
        started = perf_counter()
        try:
            response = self._transport.request(payload)
        except RetryableTransportError:
            meter.record(bound)
            raise
        except Exception:
            meter.record(bound)
            raise AccountedOperationError("provider_usage_unknown", meter.usage) from None
        finally:
            latency = (perf_counter() - started) * 1000
            call["latency_ms"] = latency
            meter.llm_wall_ms += latency
            meter.llm_calls_detail.append(call)
        known: Usage | None = None
        try:
            raw_usage = response["usage"]
            if not isinstance(raw_usage, dict):
                raise ValueError("usage_object_required")
            inputs, outputs = raw_usage["prompt_tokens"], raw_usage["completion_tokens"]
            if type(inputs) is not int or type(outputs) is not int or inputs < 0 or outputs < 0:
                raise ValueError("nonnegative_provider_usage_required")
            known = Usage(
                input_tokens=inputs,
                output_tokens=outputs,
                embedding_tokens=0,
                cost_usd=self._cost(inputs, outputs),
            )
            call["usage"] = known.model_dump(mode="json")
            call["usage_basis"] = "provider_reported"
            meter.record(known)
            if not _within(known, bound):
                raise ValueError("provider_exceeded_request_bound")
            if response["model"] != self.settings.answer_model:
                raise ValueError("provider_model_snapshot_mismatch")
            choices = response["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("single_completion_required")
            choice = choices[0]
            text = choice["message"]["content"]
            if choice["finish_reason"] != "stop" or not isinstance(text, str):
                raise ValueError("complete_text_response_required")
            if schema is not None or json_object:
                if not isinstance(json.loads(text), dict):
                    raise ValueError("structured_object_response_required")
            meter.successful_llm_calls += 1
            call["success"] = True
            return Completion(text=text, usage=known)
        except AccountedOperationError:
            raise
        except Exception:
            if known is None:
                meter.record(bound)
            raise AccountedOperationError("provider_response_contract_failed", meter.usage) from None

    def complete(
        self, messages: Sequence[Message], *, schema: dict[str, object] | None = None, json_object: bool = False
    ) -> Completion:
        if schema is not None and json_object:
            raise ValueError("one_output_format_required")
        meter = self._active()
        start = meter.usage
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self.settings.provider_attempts),
                retry=retry_if_exception_type(RetryableTransportError),
                wait=wait_exponential(multiplier=0.25, max=2),
                reraise=True,
            ):
                with attempt:
                    result = self._attempt(messages, schema, json_object)
                    return result.model_copy(
                        update={
                            "usage": Usage(
                                input_tokens=meter.usage.input_tokens - start.input_tokens,
                                output_tokens=meter.usage.output_tokens - start.output_tokens,
                                embedding_tokens=0,
                                cost_usd=meter.usage.cost_usd - start.cost_usd,
                            )
                        }
                    )
        except RetryableTransportError:
            raise AccountedOperationError("provider_attempts_exhausted", meter.usage) from None
        raise RuntimeError("completion_not_dispatched")

    def embed(self, texts: Sequence[str], *, query: bool = False) -> Embeddings:
        meter = self._active()
        if meter.embedding_calls >= self.settings.max_embedding_calls_per_operation:
            raise AccountedOperationError("operation_call_limit", meter.usage)
        # Tokenization is local, before inference. Reserve every planned chunk's
        # input tokens so overflow is refused before the model is invoked.
        tokens = self._embedder.token_count(texts, query=query)
        bound = Usage(
            input_tokens=0,
            output_tokens=0,
            embedding_tokens=tokens,
            cost_usd=Decimal(0),
        )
        if not texts or bound.embedding_tokens <= 0:
            raise AccountedOperationError("embedding_allowance_required", meter.usage)
        meter.reserve(bound)
        meter.embedding_calls += 1
        try:
            result = self._embedder.embed(texts, query=query)
            if len(result.vectors) != len(texts) or result.usage != bound:
                raise ValueError("embedding_contract_failed")
            if any(
                len(vector) != 384 or not all(isfinite(v) for v in vector) or not any(vector)
                for vector in result.vectors
            ):
                raise ValueError("invalid_embedding_vector")
        except Exception:
            meter.record(bound)
            raise AccountedOperationError("embedding_operation_failed", meter.usage) from None
        meter.record(result.usage)
        return result
