"""The Anthropic Messages protocol, over ``httpx`` and nothing else (``V2-D94``).

The gateway this project evaluates against speaks **one protocol per model family**: Claude models answer on
``/v1/messages`` and refuse the OpenAI shape with ``400 protocol_mismatch``, while the GPT models do the reverse. A
single client cannot serve both, and pretending otherwise would hide a real compatibility fact behind a retry — so the
deployment has two small clients and one selector (:func:`custom_memory_engine.integrations.provider.client_for`).

The differences that matter, and that this module handles explicitly:

* ``system`` is a top-level field, not a message with ``role: "system"``.
* the key travels in ``x-api-key`` and the API requires an ``anthropic-version`` header.
* there is no ``response_format``; JSON is obtained by asking for it in the system prompt, and the *parse* is the same
  strict step as on the OpenAI side (a model that wraps its answer in prose fails the same way).
* usage is ``input_tokens`` / ``output_tokens``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from custom_memory_engine.integrations.wire import (
    RETRYABLE_STATUS,
    Completion,
    ModelError,
    parse_json_text,
    retryable,
)

logger = structlog.get_logger(__name__)

ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True, slots=True)
class MessagesClient:
    """Minimal async Messages client with the same surface as :class:`~.openai_compatible.ChatClient`."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout: float = 120.0
    max_attempts: int = 4
    json_mode: bool = True
    temperature: float = 0.0
    transport: httpx.AsyncBaseTransport | None = None
    on_completion: Callable[[Completion], None] | None = None
    capabilities: set[str] = field(default_factory=lambda: {"temperature"})
    _client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            object.__setattr__(
                self,
                "_client",
                httpx.AsyncClient(
                    base_url=self.base_url.rstrip("/"),
                    timeout=httpx.Timeout(self.timeout, connect=10.0),
                    headers={"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION},
                    transport=self.transport,
                ),
            )
        assert self._client is not None
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            object.__setattr__(self, "_client", None)

    async def complete(self, messages: Sequence[Mapping[str, str]], *, max_tokens: int = 2048) -> Completion:
        system = "\n\n".join(str(item["content"]) for item in messages if item.get("role") == "system")
        if self.json_mode:
            system = (system + "\n\nReturn JSON only, with no prose, no markdown fence and no commentary.").strip()
        turns = [dict(item) for item in messages if item.get("role") != "system"]
        payload: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "messages": turns}
        if system:
            payload["system"] = system
        if "temperature" in self.capabilities:
            payload["temperature"] = self.temperature
        started = time.perf_counter()
        body: dict[str, Any] = {}
        adaptations = 0
        while True:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_attempts),
                wait=wait_random_exponential(multiplier=0.5, max=8),
                retry=retry_if_exception(retryable),
                reraise=True,
            ):
                with attempt:
                    response = await self.client().post("/messages", json=payload)
                    if response.status_code in RETRYABLE_STATUS:
                        response.raise_for_status()
                    if response.status_code == 400 and adaptations < 2:
                        if "temperature" in _message(response) and "temperature" in payload:
                            payload.pop("temperature", None)
                            self.capabilities.discard("temperature")
                            adaptations += 1
                            logger.info("model_parameter_unsupported", model=self.model, parameter="temperature")
                            break
                    if response.status_code >= 400:
                        raise ModelError(f"model_http_{response.status_code}")
                    body = response.json()
            else:
                break
            if body:
                break
        completion = _completion(body, self.model, (time.perf_counter() - started) * 1000)
        if self.on_completion is not None:
            self.on_completion(completion)
        return completion

    async def complete_json(self, messages: Sequence[Mapping[str, str]], *, max_tokens: int = 2048) -> Any:
        completion = await self.complete(messages, max_tokens=max_tokens)
        return parse_json_text(completion.text)


def _message(response: httpx.Response) -> str:
    try:
        document = response.json()
    except ValueError:
        return ""
    error = document.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message") or "").lower()
    return str(error or "").lower()


def _completion(body: Mapping[str, Any], fallback_model: str, latency_ms: float) -> Completion:
    blocks = body.get("content") or []
    text = "".join(str(block.get("text") or "") for block in blocks if isinstance(block, Mapping))
    usage = body.get("usage") or {}
    return Completion(
        text=text,
        model=str(body.get("model") or fallback_model),
        prompt_tokens=int(usage.get("input_tokens") or 0),
        completion_tokens=int(usage.get("output_tokens") or 0),
        latency_ms=round(latency_ms, 2),
        finish_reason=str(body.get("stop_reason") or ""),
    )


__all__ = ["ANTHROPIC_VERSION", "MessagesClient"]
