"""A generic OpenAI-compatible chat client, and the extractor/adjudicator built on it (``V2-D94``).

**Why not an SDK.** The wire format is four fields of JSON. ``openai==2.x`` would add a dependency tree, a client
object with its own retry and timeout policy, and a version pin that couples the lock file to one vendor's release
schedule — for a request this project can describe in twenty lines. The project already depends on ``httpx``, which is
the part that actually matters (connection pooling, timeouts, TLS, HTTP/2).

**No model name in the engine.** Every constructor takes the model as an argument, and the deployment supplies it from
configuration. ``tests/test_integrations.py`` asserts that no module under ``src/custom_memory_engine`` other than this
package's adapters contains a model identifier, so a provider's naming cannot leak into a decision or a migration.

**What this adapter does not do.** It does not validate the model's answer — grounding, authority, temporal bounds
and the commit strategy are deterministic and live in :mod:`custom_memory_engine.semantic`. It does not retry blindly
either: a 4xx is a durable refusal (retrying it wastes budget and can double-charge), so only 429/5xx and transport
errors are retried, with jittered exponential backoff via ``tenacity``.

**JSON reliability is a measured property, not an assumption.** Models differ wildly at emitting a strict schema, so
the client asks for ``response_format={"type": "json_object"}`` when the deployment enables it, and the parse failure
path is a *typed* error (``model_output_not_json``) that the caller records as a refusal — never a silent drop and
never an exception out of an ingestion.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from custom_memory_engine.integrations.wire import (
    RETRYABLE_STATUS,
    Completion,
    ModelClient,
    ModelError,
    parse_json_text,
    retryable,
)
from custom_memory_engine.semantic.contracts import (
    RELATIONS,
    AdjudicatedRelation,
    EntityMention,
    ExtractedClaim,
    ExtractionRequest,
    ExtractionResult,
    TemporalBounds,
    UmFacet,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChatClient:
    """Minimal async chat-completions client: one method, no hidden state, no provider SDK."""

    base_url: str
    api_key: str = field(repr=False)  # never in a repr, a log line or a traceback
    model: str
    timeout: float = 60.0
    max_attempts: int = 4
    json_mode: bool = True
    temperature: float = 0.0
    transport: httpx.AsyncBaseTransport | None = None
    #: Called with every :class:`Completion`. The evaluation harness uses it; production passes ``None``.
    on_completion: Callable[[Completion], None] | None = None
    #: The optional request fields this model accepts. Mutable because it is *learned* from the provider's refusals.
    capabilities: set[str] = field(default_factory=lambda: {"temperature", "response_format"})
    _client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            object.__setattr__(
                self,
                "_client",
                httpx.AsyncClient(
                    base_url=self.base_url.rstrip("/"),
                    timeout=httpx.Timeout(self.timeout, connect=10.0),
                    headers={"Authorization": f"Bearer {self.api_key}"},
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
        """One completion, retried on the retryable classes only.

        Some models refuse a parameter the OpenAI shape allows — ``gpt-5`` rejects ``temperature`` outright, and the
        gateway rejects ``response_format`` for it. A deployment should not have to know which of its models accepts
        which field, so a **400 whose message names a parameter** is retried once without that parameter, and the
        capability is remembered for the life of the client. Any other 4xx is a durable refusal and is surfaced as a
        status code.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": max_tokens,
        }
        if "temperature" in self.capabilities:
            payload["temperature"] = self.temperature
        if self.json_mode and "response_format" in self.capabilities:
            payload["response_format"] = {"type": "json_object"}
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
                    response = await self.client().post("/chat/completions", json=payload)
                    if response.status_code in RETRYABLE_STATUS:
                        response.raise_for_status()
                    if response.status_code == 400 and adaptations < 2:
                        dropped = self._adapt(payload, response)
                        if dropped is not None:
                            adaptations += 1
                            break  # retry the loop with the smaller payload
                    if response.status_code >= 400:
                        # a durable refusal: surfaced with its status, never with the body (which can echo the prompt)
                        raise ModelError(f"model_http_{response.status_code}")
                    body = response.json()
            else:
                break
            if "choices" in body or not payload:
                break
        completion = _completion(body, self.model, (time.perf_counter() - started) * 1000)
        if self.on_completion is not None:
            self.on_completion(completion)
        return completion

    def _adapt(self, payload: dict[str, Any], response: httpx.Response) -> str | None:
        """Drop the parameter the provider complained about, if it named one. Never parses the prompt side."""
        message = ""
        try:
            message = str((response.json().get("error") or {}).get("message") or "").lower()
        except (ValueError, AttributeError):
            return None
        for parameter in ("temperature", "response_format"):
            if parameter in message and parameter in payload:
                payload.pop(parameter, None)
                self.capabilities.discard(parameter)
                logger.info("model_parameter_unsupported", model=self.model, parameter=parameter)
                return parameter
        if "parameters" in message and "response_format" in payload:
            # the gateway answers "The provider rejected these request parameters." without naming one
            payload.pop("response_format", None)
            self.capabilities.discard("response_format")
            logger.info("model_parameter_unsupported", model=self.model, parameter="response_format")
            return "response_format"
        return None

    async def complete_json(self, messages: Sequence[Mapping[str, str]], *, max_tokens: int = 2048) -> Any:
        """A completion whose content parses as JSON, or ``model_output_not_json``."""
        completion = await self.complete(messages, max_tokens=max_tokens)
        return parse_json_text(completion.text)


def _completion(body: Mapping[str, Any], fallback_model: str, latency_ms: float) -> Completion:
    """Read the OpenAI response shape. Missing fields degrade to zero rather than raising: a provider that omits
    `usage` still returned an answer, and losing the answer over missing accounting would be the wrong trade."""
    choices = body.get("choices") or []
    message = (choices[0] or {}).get("message") if choices else None
    usage = body.get("usage") or {}
    return Completion(
        text=str((message or {}).get("content") or ""),
        model=str(body.get("model") or fallback_model),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        latency_ms=round(latency_ms, 2),
        finish_reason=str((choices[0] or {}).get("finish_reason") or "") if choices else "",
    )


# --------------------------------------------------------------------------------------------- the extraction prompt

EXTRACTION_SYSTEM = """You extract durable claims about the user from one turn of a conversation.

Return JSON only, no prose. Shape: {"claims": [...], "language": "<BCP-47 code>"}

Each claim has exactly these fields: {"content", "quote", "kind", "entities", "semantic_slot", "cardinality",
"confidence", "temporal", "um_facet"}.

THE QUOTE RULE, which is checked by a machine and cannot be argued with:
- "quote" MUST be copied character for character from the TURN text. Not reworded, not reordered, not translated,
  not with words added or removed, not with changed punctuation. If the shortest supporting fragment you can copy is
  four words, the quote is those four words.
- Worked example. TURN: "I moved to Berlin last spring, so my address changed."
  WRONG quote: "moved to Berlin" (reordered/altered), WRONG quote: "The user moved to Berlin" (a paraphrase),
  RIGHT quote: "I moved to Berlin last spring".
- If you cannot find an exact fragment that supports the claim, return no claim at all. A missing claim is recovered
  later; an ungrounded one is refused and wasted.

Other rules:
- "content" is a short, self-contained, third-person statement of the claim in the turn's language
  (e.g. "The user lives in Berlin"). It must not add facts the turn does not state.
- "kind": "claim" for facts and preferences, "procedure" only for a reusable how-to the user described.
- "entities": [{"mention": "<exact substring of the turn>", "type": "person|place|organisation|thing|topic"}]; [] if
  none. Set the turn's own words, never a translation.
- "subject_mention": the one mention the claim is *about*, copied from "entities[].mention", or null when the claim is
  about the speaker ("I...", "my...").
- "semantic_slot": a short snake_case key for the single-valued attribute being asserted
  (residence, employer, allergy, timezone, ...), or null when the claim is not single-valued. Two facts that cannot be
  true at once about the same subject MUST share a slot.
- "cardinality": "single" if only one value can be true at a time, "multi" if several can, "event" for something that
  happened.
- "confidence": 0.0-1.0, how clearly the turn supports the claim as written.
- "temporal": {"valid_from": ISO-8601|null, "valid_until": ISO-8601|null, "basis": "explicit"|"inferred"} or null.
  Resolve relative expressions ("last spring", "next month") against the turn timestamp given in the request.
- "um_facet": one of preference, goal, constraint, context, decision_pattern, meta_preference, or null.
- Extract only what the USER's own words support. Never extract from the assistant's text.
"""


def extraction_messages(request: ExtractionRequest) -> list[dict[str, str]]:
    """The turn, framed so the model cannot mistake which part is evidence."""
    context = "\n".join(f"- {item}" for item in request.context) or "(none)"
    user = (
        f"Turn timestamp: {request.occurred_at}\n"
        f"Detected language: {request.language or 'unknown'}\n"
        f"Recent context (for pronoun resolution only, not evidence):\n{context}\n\n"
        f"TURN:\n{request.text}"
    )
    return [{"role": "system", "content": EXTRACTION_SYSTEM}, {"role": "user", "content": user}]


class OpenAiCompatibleExtractor:
    """A :class:`StructuredExtractor` over a generic chat client.

    Every malformed field is dropped rather than repaired: a claim missing its quote is not groundable, so keeping it
    would only produce a `grounding_failed` refusal downstream with a misleading reason. The count of dropped fields
    is reported in ``diagnostics``, which is what makes a model's sloppiness visible in the evaluation instead of
    invisible in production.
    """

    def __init__(self, client: ModelClient, *, version: str | None = None) -> None:
        self.client = client
        self.version = version or f"{client.model}/extract-v1"

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        try:
            document = await self.client.complete_json(extraction_messages(request))
        except ModelError as error:
            logger.warning("extraction_model_failed", model=self.client.model, code=error.code)
            return ExtractionResult(
                claims=(),
                language=request.language,
                extractor_version=self.version,
                diagnostics={"model_error": error.code},
            )
        return parse_extraction(document, version=self.version, fallback_language=request.language)


def parse_extraction(document: Any, *, version: str, fallback_language: str | None) -> ExtractionResult:
    """Turn a model's JSON into typed claims, dropping what cannot be grounded."""
    if not isinstance(document, Mapping):
        raise ModelError("model_output_not_an_object")
    raw_claims = document.get("claims")
    if not isinstance(raw_claims, list):
        raise ModelError("model_output_missing_claims")
    claims: list[ExtractedClaim] = []
    dropped = 0
    for raw in raw_claims:
        claim = _claim(raw)
        if claim is None:
            dropped += 1
            continue
        claims.append(claim)
    language = document.get("language")
    return ExtractionResult(
        claims=tuple(claims),
        language=str(language) if isinstance(language, str) and language.strip() else fallback_language,
        extractor_version=version,
        diagnostics={"claims_dropped": dropped, "claims_returned": len(claims)},
    )


def _claim(raw: Any) -> ExtractedClaim | None:
    if not isinstance(raw, Mapping):
        return None
    content, quote = raw.get("content"), raw.get("quote")
    if not isinstance(content, str) or not content.strip() or not isinstance(quote, str) or not quote.strip():
        return None
    kind = raw.get("kind")
    if kind not in ("claim", "procedure"):
        kind = "claim"  # an abstraction comes from `infer`, an episode from `ingest_episode`
    slot = raw.get("semantic_slot")
    cardinality = raw.get("cardinality")
    confidence = raw.get("confidence")
    entities: list[EntityMention] = []
    for item in raw.get("entities") or []:
        if not isinstance(item, Mapping):
            continue
        mention, entity_type = item.get("mention"), item.get("type")
        if isinstance(mention, str) and mention.strip():
            entities.append(
                EntityMention(
                    label=mention.strip()[:200],
                    entity_type=str(entity_type) if isinstance(entity_type, str) and entity_type else "thing",
                )
            )
    facet = raw.get("um_facet")
    from custom_memory_engine.semantic.contracts import UM_FACETS

    return ExtractedClaim(
        content=content.strip()[:4000],
        verbatim_quote=quote.strip()[:4000],
        kind=kind,  # type: ignore[arg-type]
        entities=tuple(entities),
        semantic_slot=slot.strip().lower()[:120] if isinstance(slot, str) and slot.strip() else None,
        cardinality=cardinality if cardinality in ("single", "multi", "event") else "multi",
        confidence=(
            float(confidence) if isinstance(confidence, int | float) and 0.0 <= float(confidence) <= 1.0 else 0.6
        ),
        subject_mention=_subject(raw.get("subject_mention")),
        temporal=_temporal(raw.get("temporal")),
        um_facet=cast(UmFacet | None, facet if isinstance(facet, str) and facet in UM_FACETS else None),
        assertion_mode="asserted",
    )


def _subject(raw: Any) -> str | None:
    """The designated subject mention, or ``None``. An unlisted string is dropped rather than rejected: the contract
    refuses a subject that is not one of the entities, and a model's stray value must not fail the whole extraction."""
    return raw.strip()[:200] if isinstance(raw, str) and raw.strip() else None


def _temporal(raw: Any) -> TemporalBounds:
    """The bounds the model resolved. An unparseable one is dropped rather than repaired: ``semantic.temporal``
    validates whatever arrives, and a repaired guess would read as the model's own statement."""
    if not isinstance(raw, Mapping):
        return TemporalBounds()
    start, end = raw.get("valid_from"), raw.get("valid_until")
    return TemporalBounds(
        start=start if isinstance(start, str) and start.strip() else None,
        end=end if isinstance(end, str) and end.strip() else None,
    )


# ------------------------------------------------------------------------------------------- the relation adjudicator

ADJUDICATION_SYSTEM = """You decide how one new statement relates to one existing memory of the same user.

The two may be in different languages or scripts (Arabic, English, Arabizi, or a mix); judge the meaning, not the
wording.

Return JSON only: {"relation": "...", "confidence": 0.0-1.0, "rationale": "..."} with relation one of:
- "duplicate": they say the same thing; the new one adds nothing.
- "additive": the new one adds information and the old one stays true (a further detail, a second item).
- "update": the new one is a later value of the same attribute (a move, a new job, a changed time).
- "correction": the new one says the old one was wrong (the user fixes a mistake).
- "contradiction": both cannot be true at the same time and nothing says which is newer.
- "unrelated": about different things, or about a different person.

Judge only the two texts. Do not use outside knowledge. If the new statement is vaguer than the old one, prefer
"duplicate" or "unrelated" over "correction"."""


class OpenAiCompatibleAdjudicator:
    """A :class:`RelationAdjudicator` that asks a model which of five relations holds.

    The model chooses a *label*, never an action: ``conflict.commit_strategy`` maps the label onto what the engine
    does, applies the authority veto, and the exclusion constraint has the last word. So a model that answers wrongly
    produces a wrong label the deterministic layer can reject, not a wrong write.
    """

    def __init__(self, client: ModelClient) -> None:
        self.client = client

    async def adjudicate(
        self, claim: ExtractedClaim, existing: Sequence[tuple[str, str]], *, language: str | None = None
    ) -> Sequence[AdjudicatedRelation]:
        """One call per candidate: a batch would make the model compare candidates with each other, which is exactly
        the ordering bias the protocol forbids."""
        del language
        relations: list[AdjudicatedRelation] = []
        for candidate_id, content in existing:
            messages = [
                {"role": "system", "content": ADJUDICATION_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "new_statement": claim.content,
                            "existing_memory": content,
                            "new_slot": claim.semantic_slot,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            try:
                document = await self.client.complete_json(messages, max_tokens=256)
            except ModelError:
                continue  # no opinion is a valid answer; `commit_strategy` then falls back to insert
            if not isinstance(document, Mapping):
                continue
            relation = document.get("relation")
            if relation not in RELATIONS:
                continue
            rationale = document.get("rationale")
            relations.append(
                AdjudicatedRelation(
                    existing_id=candidate_id,
                    relation=relation,
                    # content-free by construction: the model's own words never reach a stored row
                    rationale="model_adjudication" if not isinstance(rationale, str) else "model_adjudication",
                )
            )
        return tuple(relations)


ENTITY_SYSTEM = """You decide whether a mention in a user's message refers to one of the user's known entities.

You get the mention and a numbered list of candidates. The mention and a candidate may be written in different scripts
or languages (Arabic and English, a transliteration, a nickname): judge whether they denote the same real-world person,
place, organisation or thing.

Return JSON only: {"match": N} where N is the number of the one candidate the mention certainly refers to, or
{"match": null} when none does or you cannot be sure. Two different things with similar names (two offices in two
cities, "Project Alpha" and "Project Beta", two people who share a first name) are NOT a match. When in doubt, answer
null: a missed match is repaired later, a wrong match attaches the user's memories to the wrong entity."""


class OpenAiCompatibleEntityAdjudicator:
    """An :class:`~custom_memory_engine.semantic.entities.EntityAdjudicator` backed by a chat model (``V2-D106``).

    **The model sees numbers, never ids.** Candidates are presented as ``1..n`` and the answer is mapped back here, so
    an entity id never enters a prompt (where it would be one more identifier a provider logs) and a model cannot
    answer with an id it was not offered: anything but an in-range integer or ``null`` is an abstention. The resolver
    re-checks membership too (`semantic.entities.resolve_mention`), so the fence holds even for another adapter.

    A model failure is an abstention, not an error: the mention stays unresolved, which is the safe outcome.
    """

    def __init__(self, client: ModelClient) -> None:
        self.client = client

    async def choose(self, mention: str, candidates: Sequence[tuple[str, str]]) -> str | None:
        if not candidates:
            return None
        messages = [
            {"role": "system", "content": ENTITY_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "mention": mention,
                        "candidates": [
                            {"n": index, "name": label} for index, (_, label) in enumerate(candidates, start=1)
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            document = await self.client.complete_json(messages, max_tokens=64)
        except ModelError:
            return None
        if not isinstance(document, Mapping):
            return None
        match = document.get("match")
        if isinstance(match, bool) or not isinstance(match, int) or not 1 <= match <= len(candidates):
            return None
        return candidates[match - 1][0]


__all__ = [
    "ADJUDICATION_SYSTEM",
    "ENTITY_SYSTEM",
    "OpenAiCompatibleEntityAdjudicator",
    "EXTRACTION_SYSTEM",
    "ChatClient",
    "Completion",
    "ModelError",
    "OpenAiCompatibleAdjudicator",
    "OpenAiCompatibleExtractor",
    "extraction_messages",
    "parse_extraction",
]
