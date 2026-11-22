"""One selector for two wire protocols (``V2-D94``).

The gateway this project evaluates against routes by *model family*, not by path: Claude models answer on
``/v1/messages`` and refuse the OpenAI shape with ``400 protocol_mismatch``; GPT models answer on
``/v1/chat/completions`` and refuse Messages. That is a property of the deployment, not of the engine, so it lives
here — one function, one table, no branching on a profile or a vendor inside the engine.

``protocol`` is explicit when the deployment knows, and the default guesses from the model id (``claude*`` is
Messages). A guess is best-effort by design: :func:`client_for` never *probes*, because a probe is a billed request;
when the guess is wrong the first call fails with ``protocol_mismatch`` and the deployment sets ``CME_LLM_PROTOCOL``.
That is a better failure than a silent, charged retry against the wrong endpoint.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from custom_memory_engine.integrations.anthropic_messages import MessagesClient
from custom_memory_engine.integrations.openai_compatible import ChatClient
from custom_memory_engine.integrations.wire import Completion, ModelClient

type WireProtocol = Literal["openai", "anthropic"]


def protocol_for(model: str, *, explicit: str | None = None) -> WireProtocol:
    """The wire protocol for ``model``: explicit if given, else family-guessed, else OpenAI."""
    if explicit in ("openai", "anthropic"):
        return explicit  # type: ignore[return-value]
    return "anthropic" if model.lower().startswith(("claude", "anthropic/")) else "openai"


def client_for(
    *,
    base_url: str,
    api_key: str,
    model: str,
    protocol: str | None = None,
    json_mode: bool = True,
    timeout: float = 120.0,
    on_completion: Callable[[Completion], None] | None = None,
) -> ModelClient:
    """Build the client for one model. Same surface either way, so nothing downstream knows which it got."""
    chosen = protocol_for(model, explicit=protocol)
    if chosen == "anthropic":
        return MessagesClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
            json_mode=json_mode,
            on_completion=on_completion,
        )
    return ChatClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        json_mode=json_mode,
        on_completion=on_completion,
    )


__all__ = ["ModelClient", "WireProtocol", "client_for", "protocol_for"]
