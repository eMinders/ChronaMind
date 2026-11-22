"""Long-term memory over the ChronaMind memory engine, replacing the ``mem0ai`` client (``V2-D93``).

The root application has two problems with its memory layer, and the review named both:

* ``mem0ai`` (the OSS Python engine, v2.2.1) writes ADD-only with no supersession, keeps deleted plaintext in its SQLite
  ``history``/``messages`` tables, and does not check tenant ownership on ``get``/``delete(id)``. It cannot express the
  guarantees the FastAPI agent is being built on top of.
* The engine and the app use **different interpreters on purpose** — CPython 3.12 for the engine (its own Ruff, Pyright,
  lock file and CI job: ``PLAN/INDEPENDENCE_BOUNDARY.md`` §4) against 3.14 for the app. An in-process import would erase
  that boundary and drag the engine's dependency set into the app.

So the boundary is a **narrow HTTP contract over the engine's own public operations**, and this module is the app's side
of it. Nothing here knows how the engine stores anything: it sends ``query``, ``ingest_episode`` and ``assert`` and reads
the four channels back. That is deliberate — the engine's value is its contract, and a client that reached past it would
be the same mistake mem0 made with its own storage.

**What the agent actually needs** is three methods, and each maps onto one operation:

=====================  ==================================  ====================================================
agent call             engine operation                    notes
=====================  ==================================  ====================================================
``initialize()``       ``GET /healthz``                    a pre-warm, not a migration; failure is logged, not fatal
``search(user, text)``  ``query`` with ``text``             renders ``facts`` and ``hypotheses`` as a context block,
                                                           ``conflicts`` as an explicit warning
``add(user, messages)`` ``ingest_episode`` + ``assert``     the turn is recorded first (fast lane), then each
                                                           user-role message becomes a grounded claim
=====================  ==================================  ====================================================

**Why search renders rather than returning JSON.** The LangGraph node pastes the result into a prompt, so the shape is a
string. What matters is that the *channels* survive the rendering: a hypothesis is labelled as one, and an open
contradiction is shown as a warning rather than silently resolved. A flat list of "memories" — which is what mem0
returns — is exactly the epistemic flattening the engine exists to prevent.

**What is not here yet, stated rather than implied.** The engine side of this contract (an HTTP host that authenticates a
caller and dispatches to ``Engine.call``) does not exist; it is tracked as an acceptance criterion of ``E-046`` in
``custom-memory-engine/PLAN/V2/ROADMAP_V2.md`` §6.1. Until it lands, a deployment that configures
``MEMORY_ENGINE_URL`` gets a client that cannot connect, and one that does not gets the legacy path — never a silent
half-migration.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

import httpx

from app.core.cache import cache_key, cache_service
from app.core.config import settings
from app.core.logging import logger

#: The four channels the engine returns, and how each is rendered into prompt context. A hypothesis is never rendered
#: as a fact, and a conflict is never rendered as a fact either: that separation is the engine's product guarantee.
_CHANNEL_LABELS: dict[str, str] = {
    "facts": "Known facts",
    "hypotheses": "Hypotheses (inferred, not confirmed)",
    "procedures": "Procedures that applied",
    "conflicts": "Open contradictions (unresolved)",
}

type Backend = Literal["engine", "none"]


def canonical_hash(text: str) -> str:
    """The engine's canonical content hash: NFC, LF newlines, UTF-8, SHA-256.

    Duplicated from the engine's ``grounding.sources.canonical_hash`` on purpose: the two projects share a wire contract,
    not an import (``PLAN/INDEPENDENCE_BOUNDARY.md``). A test pins the two together
    (``tests/test_memory_engine_boundary.py::test_the_content_hash_matches_the_engines_canonical_form``).
    """
    import unicodedata

    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class MemoryEngineClient:
    """The app's memory client: the engine's public operations over HTTP, with the cache in front of reads."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Build the client. ``transport`` exists so a test can drive the wire contract without a server."""
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # -- configuration -----------------------------------------------------------------------------------------

    @property
    def backend(self) -> Backend:
        """Which backend is configured. There is no third option: a missing URL is *no* long-term memory."""
        return "engine" if getattr(settings, "MEMORY_ENGINE_URL", "") else "none"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.MEMORY_ENGINE_URL.rstrip("/"),
                timeout=httpx.Timeout(5.0, connect=2.0),
                headers={"Authorization": f"Bearer {settings.MEMORY_ENGINE_TOKEN}"}
                if getattr(settings, "MEMORY_ENGINE_TOKEN", "")
                else {},
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        """Close the pooled connection. Called from the app's shutdown hook."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- the agent's three calls -------------------------------------------------------------------------------

    async def initialize(self) -> None:
        """Pre-warm: one health probe so the first turn does not pay connection setup.

        A failure is logged and swallowed, exactly as the mem0 implementation did: long-term memory is an enhancement,
        and an agent that refuses to answer because its memory service is down is a worse product.
        """
        if self.backend == "none":
            logger.info("memory_engine_not_configured", backend=self.backend)
            return
        try:
            response = await self._http().get("/healthz")
            response.raise_for_status()
            logger.info("memory_engine_initialized", status=response.status_code)
        except Exception as error:  # noqa: BLE001 - a pre-warm must never stop the app from serving
            logger.error("memory_engine_pre_warm_failed", error=str(error))

    async def search(self, user_id: str | None, query: str) -> str:
        """Render the caller's memory as prompt context, or ``""`` when there is none or the engine is unreachable.

        An anonymous session gets no long-term memory rather than a shared partition, which is what the mem0
        implementation did and is still right: a shared partition would leak one caller's facts into another's prompt.
        """
        if user_id is None or self.backend == "none":
            return ""
        key = cache_key("memory", str(user_id), query)
        cached = await cache_service.get(key)
        if cached is not None:
            logger.debug("memory_search_cache_hit", user_id=user_id)
            return cached
        try:
            response = await self._http().post(
                "/v1/call",
                json={
                    "operation": "query",
                    "tier": "B",
                    "arguments": {
                        "request": {"text": query, "scope": f"/u/{user_id}/", "limit": 20},
                    },
                },
            )
            response.raise_for_status()
            rendered = _render(response.json())
            if rendered:
                await cache_service.set(key, rendered)
            return rendered
        except Exception as error:  # noqa: BLE001 - memory is an enhancement, never a hard dependency
            logger.exception("failed_to_get_relevant_memory", user_id=user_id, error=str(error))
            return ""

    async def add(self, user_id: str | None, messages: list[dict], metadata: dict | None = None) -> None:
        """Record the conversation: the turn as an episode, then each user message as a grounded claim.

        The episode is written first, so the turn is durable and inside the deletion boundary even if the claim step
        fails. Only ``role == "user"`` messages become claims: an assistant's text is not evidence about the user, and
        the engine would (correctly) rank it below the user's own words anyway.
        """
        if user_id is None or self.backend == "none" or not messages:
            return
        scope = f"/u/{user_id}/"
        del metadata  # carried by the turn in a later protocol revision; nothing is silently dropped today
        try:
            for message in messages:
                text = str(message.get("content") or "")
                if not text.strip():
                    continue
                turn_id = f"turn-{canonical_hash(text)[:32]}"
                await self._post(
                    "ingest_episode",
                    {
                        "scope": scope,
                        "content": text,
                        "source": {
                            "type": "external",
                            "ref": f"app://session/{turn_id}",
                            "span": text[:64],
                            "content_hash": canonical_hash(text),
                            # The app keeps the transcript, so it is the one that knows who spoke. Without this the
                            # engine would quarantine the claim as third-party evidence (`V2-D96`).
                            "role": "assistant" if message.get("role") == "assistant" else "user",
                        },
                        "event_time": message.get("occurred_at"),
                        "idempotency_key": f"episode:{turn_id}",
                    },
                )
                if message.get("role") == "user":
                    await self._post(
                        "assert",
                        {
                            "scope": scope,
                            "kind": "claim",
                            "content": text,
                            "evidence": [
                                {
                                    "type": "external",
                                    "ref": f"app://session/{turn_id}",
                                    "span": text[:64],
                                    "content_hash": canonical_hash(text),
                                    "role": "user",
                                }
                            ],
                            "event_range": None,
                            # a single-valued claim with no slot is the safe default: it can supersede, and the
                            # semantic layer sets the slot when an extractor is bound
                            "cardinality": "multi",
                            "idempotency_key": f"claim:{turn_id}",
                        },
                    )
            logger.info("long_term_memory_updated_successfully", user_id=user_id, messages=len(messages))
        except Exception as error:  # noqa: BLE001 - see `search`
            logger.exception("failed_to_update_long_term_memory", user_id=user_id, error=str(error))

    async def _post(self, operation: str, arguments: dict[str, Any]) -> None:
        response = await self._http().post(
            "/v1/call",
            json={"operation": operation, "tier": "B", "arguments": arguments},
        )
        response.raise_for_status()


def _render(payload: dict[str, Any]) -> str:
    """Render the engine's four channels as prompt context, keeping the epistemic labels.

    A conflict is rendered as *unresolved*, never as a fact, and a hypothesis is labelled as inferred. That is the whole
    difference between this and a flat "memories" list: a downstream model can tell what it is allowed to rely on.
    """
    lines: list[str] = []
    for channel, label in _CHANNEL_LABELS.items():
        # The label is emitted only when the channel contributes a line: an empty or blank-only channel must not leave
        # a heading with nothing under it, which reads to a model as "there is a fact here" and is exactly the kind of
        # misreading the channel separation exists to prevent.
        entries = [
            f"* {content.strip()}"
            for content in (str(item.get("content") or "") for item in payload.get(channel) or [])
            if content.strip()
        ]
        if entries:
            lines.append(f"{label}:")
            lines.extend(entries)
    return "\n".join(lines)


memory_engine = MemoryEngineClient()
