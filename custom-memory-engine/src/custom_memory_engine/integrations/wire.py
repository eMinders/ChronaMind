"""The wire primitives both protocol clients share (``V2-D94``).

Separated from either client so the two can be imported in any order and neither depends on the other. Everything here
is protocol-neutral: two dataclasses, one retry predicate, one JSON parse, and the :class:`ModelClient` protocol the
extractor and the adjudicator are typed against.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

#: Statuses worth retrying. A 4xx that is not here is a durable refusal: retrying it wastes budget and can double-bill.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class ModelError(RuntimeError):
    """A static code, never a fragment of the prompt or the response."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Completion:
    """One model call, with the accounting a deployment needs to cost it."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS
    return False


def parse_json_text(text: str) -> Any:
    """The one JSON parse both protocols use, so their failure modes are identical.

    A fenced block is tolerated because it is what models do, not because the contract allows it: the schema check
    downstream still decides whether the answer is usable, so stripping a fence cannot promote a bad answer. A brace
    scan recovers an object embedded in prose -- the same tolerance, no more.
    """
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(body or "null")
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(body[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ModelError("model_output_not_json") from None


@runtime_checkable
class ModelClient(Protocol):
    """What the extractor and the adjudicator need: a model name, one call, and a JSON call."""

    @property
    def model(self) -> str: ...

    async def complete(self, messages: Sequence[Mapping[str, str]], *, max_tokens: int = 2048) -> Completion: ...

    async def complete_json(self, messages: Sequence[Mapping[str, str]], *, max_tokens: int = 2048) -> Any: ...

    async def aclose(self) -> None: ...


__all__ = ["RETRYABLE_STATUS", "Completion", "ModelClient", "ModelError", "parse_json_text", "retryable"]
