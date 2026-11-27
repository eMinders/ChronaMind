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

**The wire contract.** Every operation is ``POST {MEMORY_ENGINE_URL}/v1/call`` with ``Authorization: Bearer
{MEMORY_ENGINE_TOKEN}`` and the body ``{"operation", "principal", "arguments"}``. The token is a *delegating* service
token on the engine host (``CME_HOST_TOKENS``, ``"delegate": true``): the tier comes from the token, and ``principal``
names the end user, who becomes the engine caller with their own ``/u/<user_id>/`` namespace. The answer is
``{"result": {...}}``, or ``{"error": {"code", "detail"?}}`` with a 4xx/5xx status. The engine's host serves every
operation; ``tests/integration/test_app_engine_boundary.py`` drives this client against a real host and database, and
``tests/test_memory_engine_boundary.py`` pins the request shapes without one.

**What the agent actually needs** is three methods, and each maps onto one operation:

=====================  ==================================  ====================================================
agent call             engine operation                    notes
=====================  ==================================  ====================================================
``initialize()``       ``GET /healthz``                    a pre-warm, not a migration; failure is logged, not fatal
``search(user, text)``  ``query`` with ``text``             renders the channels as a labelled context block,
                                                           ``conflicts`` as an explicit warning
``add(user, messages)`` ``ingest_episode`` + ``assert``     the turn is recorded first (fast lane), then each
                                                           user-role message becomes a grounded claim
=====================  ==================================  ====================================================

**Why search renders rather than returning JSON.** The LangGraph node pastes the result into a prompt, so the shape is a
string. What matters is that the *channels* survive the rendering: a hypothesis is labelled as one, an open
contradiction is shown as a warning rather than silently resolved, and a recorded conversation turn (an ``episode``,
which the engine returns in its ``facts`` channel as "what was said") is shown as an earlier message, never as a known
fact. A flat list of "memories" — which is what mem0 returns — is exactly the epistemic flattening the engine exists to
prevent.

**Nothing is cached** (``V2-D116``). A rendered context is plaintext memory content; a client-side copy would outlive
an engine-side deletion, retraction or supersession, so every search asks the engine.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any, Literal

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.services.memory_slots import (
    MULTI,
    NullSlotClassifier,
    SlotClassification,
    SlotClassifier,
    configured_classifier,
)

#: The four channels the engine returns, and how each is rendered into prompt context. A hypothesis is never rendered
#: as a fact, and a conflict is never rendered as a fact either: that separation is the engine's product guarantee.
_CHANNEL_LABELS: dict[str, str] = {
    "facts": "Known facts",
    "hypotheses": "Hypotheses (inferred, not confirmed)",
    "procedures": "Procedures that applied",
    "conflicts": "Open contradictions (unresolved)",
}
#: Where a recorded turn (``kind == "episode"`` in the ``facts`` channel) is rendered instead of "Known facts". An
#: episode is verbatim conversation — the user's or the assistant's words, which the result does not attribute — and it
#: is never superseded, so an old turn can contradict the current belief. The label says all three things.
_EPISODE_LABEL = "Earlier conversation (verbatim; may be outdated; not confirmed facts)"

type Backend = Literal["engine", "none"]


def canonical_hash(text: str) -> str:
    """The engine's canonical content hash: NFC, LF newlines, UTF-8, SHA-256.

    Duplicated from the engine's ``grounding.sources.canonical_hash`` on purpose: the two projects share a wire contract,
    not an import (``PLAN/INDEPENDENCE_BOUNDARY.md``). A test pins the two together
    (``tests/test_memory_engine_boundary.py::test_the_content_hash_matches_the_engines_canonical_form``).
    """
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class EngineCallError(Exception):
    """An engine refusal, or an unreachable host, raised by :meth:`MemoryEngineClient.call`.

    ``code`` is the engine's static error code (``validation_error``, ``forbidden``, ``precondition_failed``,
    ``resource_limit``, ``retryable_conflict``, ``internal_error``) or ``engine_unavailable`` when no answer arrived.
    ``detail`` is the host's static sub-code, if it sent one. ``status`` is the HTTP status, ``0`` when there was none.
    """

    def __init__(self, code: str, detail: str | None, status: int) -> None:
        """Carry the code, the optional sub-code and the HTTP status."""
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.status = status


class MemoryEngineClient:
    """The app's memory client: the engine's public operations over HTTP, acting for one end user per call."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        slot_classifier: SlotClassifier | None = None,
        token_setting: str = "MEMORY_ENGINE_TOKEN",
        read_timeout: float = 5.0,
    ) -> None:
        """Build the client.

        ``transport`` exists so a test can drive the wire contract without a server; ``slot_classifier`` so a test can
        supply recorded classifications. The default classifier is the one the settings select (``V2-D121``).
        ``token_setting`` names the setting that holds this client's bearer token: the agent's client uses the Tier B
        ``MEMORY_ENGINE_TOKEN``, the inspector the Tier C ``MEMORY_ENGINE_USER_TOKEN``. The tier is never sent; the
        host derives it from the token (``T-34``).
        """
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._slot_classifier = slot_classifier if slot_classifier is not None else configured_classifier()
        self._token_setting = token_setting
        self._read_timeout = read_timeout

    # -- configuration -----------------------------------------------------------------------------------------

    @property
    def backend(self) -> Backend:
        """Which backend is configured. There is no third option: a missing URL is *no* long-term memory."""
        return "engine" if getattr(settings, "MEMORY_ENGINE_URL", "") else "none"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            token = getattr(settings, self._token_setting, "")
            self._client = httpx.AsyncClient(
                base_url=settings.MEMORY_ENGINE_URL.rstrip("/"),
                timeout=httpx.Timeout(self._read_timeout, connect=2.0),
                headers={"Authorization": f"Bearer {token}"} if token else {},
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
            logger.exception("memory_engine_pre_warm_failed", error=str(error))

    async def search(self, user_id: str | None, query: str) -> str:
        """Render the caller's memory as prompt context, or ``""`` when there is none or the engine is unreachable.

        An anonymous session gets no long-term memory rather than a shared partition, which is what the mem0
        implementation did and is still right: a shared partition would leak one caller's facts into another's prompt.
        Every call asks the engine; nothing is cached (``V2-D116``).
        """
        if user_id is None or self.backend == "none":
            return ""
        try:
            result = await self._post(
                user_id,
                "query",
                {"request": {"text": query, "scope": f"/u/{user_id}/", "limit": 20}},
            )
            return _render(result)
        except Exception as error:  # noqa: BLE001 - memory is an enhancement, never a hard dependency
            logger.exception("failed_to_get_relevant_memory", user_id=user_id, error=str(error))
            return ""

    async def add(
        self,
        user_id: str | None,
        messages: list[dict],
        metadata: dict | None = None,
        *,
        session_id: str | None = None,
        history_epoch: int = 0,
    ) -> None:
        """Record the conversation: the turn as an episode, then each user message as a grounded claim.

        The agent passes the session's **whole** history on every turn, so each message needs an identity that is
        stable across those resends (a resend must replay, not duplicate) yet distinct for the same words said again
        later (a user who returns to "I live in Berlin" after "I moved to Paris" must win the slot back, not replay
        the superseded claim). With ``session_id`` the identity is (session, ``history_epoch``, position, role, text).
        Position alone is not enough: the history is append-only *within* an epoch, but ``DELETE /messages`` clears it
        and restarts the positions, so without the epoch a message repeated afterwards would collide with the one
        before the clear and replay a claim that had already been superseded. Without a session, the text alone is the
        identity, which dedupes resends but cannot tell a repeated statement from a resent one.

        The episode is written first, so the turn is durable and inside the deletion boundary even if the claim step
        fails. Only ``role == "user"`` messages become claims: an assistant's text is not evidence about the user, and
        the engine would (correctly) rank it below the user's own words anyway.

        Only the **newest** user message is classified for a single-valued slot (``V2-D121``): it is the one this call
        records for the first time. Every earlier user message was the newest one of an earlier call, so its ``assert``
        replays by idempotency key and its arguments are not read again; classifying it would cost a model call per
        message per turn for nothing. If an earlier call failed outright, such a message is recorded ``multi`` here,
        which is the safe direction (it supersedes nothing).
        """
        if user_id is None or self.backend == "none" or not messages:
            return
        scope = f"/u/{user_id}/"
        del metadata  # carried by the turn in a later protocol revision; nothing is silently dropped today
        newest_user = max(
            (index for index, message in enumerate(messages) if message.get("role") == "user"), default=None
        )
        try:
            for position, message in enumerate(messages):
                text = str(message.get("content") or "")
                if not text.strip():
                    continue
                turn_id = _turn_id(session_id, position, str(message.get("role") or ""), text, history_epoch)
                await self._post(
                    user_id,
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
                    classification = MULTI
                    if position == newest_user:
                        context = [str(earlier.get("content") or "") for earlier in messages[:position]]
                        classification = await self._slot_classifier.classify(text, context)
                    await self._post(user_id, "assert", _claim_arguments(scope, text, turn_id, classification))
            logger.info("long_term_memory_updated_successfully", user_id=user_id, messages=len(messages))
        except Exception as error:  # noqa: BLE001 - see `search`
            logger.exception("failed_to_update_long_term_memory", user_id=user_id, error=str(error))

    async def health(self) -> bool:
        """Whether the host answers its content-free ``/healthz`` with 200. Never raises."""
        try:
            response = await self._http().get("/healthz")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def call(self, user_id: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One operation acting for ``user_id``, with every refusal raised as :class:`EngineCallError`.

        Unlike :meth:`search` and :meth:`add`, which swallow failures because memory is an enhancement on the agent's
        path, the inspector routes must show the user what the engine answered, so nothing is swallowed here.
        """
        envelope = {"operation": operation, "principal": user_id, "arguments": arguments}
        try:
            response = await self._http().post("/v1/call", json=envelope)
        except httpx.HTTPError:
            raise EngineCallError("engine_unavailable", None, 0) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise EngineCallError("internal_error", None, response.status_code)
        if response.status_code != 200:
            error = body.get("error")
            error = error if isinstance(error, dict) else {}
            detail = error.get("detail")
            raise EngineCallError(
                str(error.get("code") or "internal_error"),
                detail if isinstance(detail, str) else None,
                response.status_code,
            )
        result = body.get("result")
        if not isinstance(result, dict):
            raise EngineCallError("internal_error", "response_without_result", response.status_code)
        return result

    async def _post(self, user_id: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One engine call acting for ``user_id``; returns the ``result`` object, raising on any refusal."""
        response = await self._http().post(
            "/v1/call",
            json={"operation": operation, "principal": user_id, "arguments": arguments},
        )
        response.raise_for_status()
        result = response.json().get("result")
        if not isinstance(result, dict):
            raise ValueError("memory_engine_response_without_result")
        return result


def _turn_id(session_id: str | None, position: int, role: str, text: str, history_epoch: int = 0) -> str:
    """The message's stable identity: (session, epoch, position, role, text) when the session is known, else the text."""
    identity = text if session_id is None else "\x1f".join((session_id, str(history_epoch), str(position), role, text))
    return f"turn-{canonical_hash(identity)[:32]}"


def _claim_arguments(scope: str, text: str, turn_id: str, classification: SlotClassification) -> dict[str, Any]:
    """The ``assert`` arguments for one user message, with the cardinality the classifier justified (``V2-D115``).

    A message the slot classifier recognises as the speaker's current value of a slot ("I moved to Paris",
    "انتقلت إلى باريس") is sent as ``cardinality="single"`` with its ``semantic_slot``, so the engine supersedes the
    previous value of that slot. Everything else is ``"multi"`` with no slot: a single-valued claim *without* a slot
    would make every such message exclusive with every other one.
    """
    arguments: dict[str, Any] = {
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
        "cardinality": classification.cardinality,
        "idempotency_key": f"claim:{turn_id}",
    }
    if classification.semantic_slot is not None:
        arguments["semantic_slot"] = classification.semantic_slot
    return arguments


def _lines(items: object) -> list[str]:
    """The non-blank contents of one channel's items, in the engine's order."""
    if not isinstance(items, list):
        return []
    contents = (str(item.get("content") or "").strip() for item in items if isinstance(item, dict))
    return [content for content in contents if content]


def _render(result: dict[str, Any]) -> str:
    """Render the engine's ``query`` result as prompt context, keeping the epistemic labels.

    A conflict is rendered as *unresolved*, never as a fact, and a hypothesis is labelled as inferred. Within the
    ``facts`` channel, only non-episode items (claims the user asserted) are "Known facts"; a recorded turn is rendered
    under :data:`_EPISODE_LABEL` and dropped when its text is already rendered elsewhere (the claim grounded in a turn
    carries the same text). That is the whole difference between this and a flat "memories" list: a downstream model
    can tell what it is allowed to rely on.
    """
    facts = result.get("facts")
    fact_items = facts if isinstance(facts, list) else []
    sections: dict[str, list[str]] = {
        label: _lines(
            [item for item in fact_items if isinstance(item, dict) and item.get("kind") != "episode"]
            if channel == "facts"
            else result.get(channel)
        )
        for channel, label in _CHANNEL_LABELS.items()
    }
    rendered = {line for lines in sections.values() for line in lines}
    episodes = _lines([item for item in fact_items if isinstance(item, dict) and item.get("kind") == "episode"])
    sections[_EPISODE_LABEL] = [line for line in dict.fromkeys(episodes) if line not in rendered]
    output: list[str] = []
    for label, lines in sections.items():
        # A label is emitted only when its section has a line: an empty heading reads to a model as "there is a fact
        # here", which is exactly the kind of misreading the channel separation exists to prevent.
        if lines:
            output.append(f"{label}:")
            output.extend(f"* {line}" for line in lines)
    return "\n".join(output)


memory_engine = MemoryEngineClient()

#: The memory inspector's client (`app/api/v1/memory.py`): the Tier C delegating token, a longer read timeout (a
#: `delete` walks the whole provenance closure), and no slot classifier, because it never writes a turn.
memory_inspector = MemoryEngineClient(
    slot_classifier=NullSlotClassifier(),
    token_setting="MEMORY_ENGINE_USER_TOKEN",
    read_timeout=15.0,
)
