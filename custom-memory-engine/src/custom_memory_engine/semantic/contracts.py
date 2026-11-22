"""The extraction contract: what a model must return, typed, and the seam it is injected through (``V2-D88``).

This module is the answer to the question the review asked first -- *where does the LLM create genuine, irreplaceable
value?* -- expressed as data rather than as prose. Everything a model is asked for is here; everything the engine
then checks is in the sibling modules. Nothing here imports a provider.

**Why the schema is closed and the vocabulary inside it is open.** ``kind``, ``assertion_mode``, ``cardinality`` and
``um_facet`` are small closed sets because they are *epistemic and structural*: a claim is asserted or hedged, an
artifact is a claim or a procedure, and a value about a person is single- or multi-valued. Letting a model invent a
mode would make the lifecycle table meaningless. ``semantic_slot``, ``entities`` and the claim text, by contrast, are
**open**: "residence", "preferred_editor" and "meeting_time_constraint" are not in any catalog and never should be,
because a general-purpose memory engine cannot enumerate the domains its users will live in. That is exactly the
distinction V1 got wrong, by making a database foreign key the only way to say what a claim was about.

**Why the quote is in the source language.** ``verbatim_quote`` must be copied from the source, not translated. A
translated quote cannot be grounded deterministically -- there is nothing to compare it to -- so grounding would
degrade to trusting the model. Keeping the original means a claim can be stored in one language, retrieved by a
question in another (via the multilingual arms), and *proved* against what was actually said.

**Why ``.strip()`` fields are refused rather than repaired.** A model that returns ``"  residence "`` is a model whose
output the engine cannot compare with the normalized form it will store. Silently trimming hides a mismatch that
:mod:`~custom_memory_engine.semantic.slots` exists to make impossible; refusing it makes the contract falsifiable in
:mod:`tests.test_semantic_contracts`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

type AssertionMode = Literal["asserted", "hedged", "hypothetical", "planned", "quoted", "third_party", "joking"]
type Cardinality = Literal["single", "multi", "event"]
type UmFacet = Literal["preference", "goal", "constraint", "context", "decision_pattern", "meta_preference"]
type ClaimKind = Literal["claim", "procedure"]
type Relation = Literal["duplicate", "update", "correction", "contradiction", "additive", "unrelated"]

#: The closed structural vocabularies, restated here so a caller can validate a payload without importing the schema
#: module (which pulls in psycopg). ``tests/test_semantic_contracts.py`` pins each one to its canonical home.
ASSERTION_MODES: frozenset[str] = frozenset(AssertionMode.__value__.__args__)
CARDINALITIES: frozenset[str] = frozenset(Cardinality.__value__.__args__)
UM_FACETS: frozenset[str] = frozenset(UmFacet.__value__.__args__)
CLAIM_KINDS: frozenset[str] = frozenset(ClaimKind.__value__.__args__)
RELATIONS: frozenset[str] = frozenset(Relation.__value__.__args__)


class ContractError(ValueError):
    """A static code for a payload that cannot enter the write path; never carries the payload."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TemporalBounds:
    """When the claim is *about*, as instants the model resolved against the turn's own ``occurred_at``.

    The engine never parses a date phrase. Relative expressions ("last spring", "الأسبوع الماضي", "先週") are the
    model's job precisely because there is no language-agnostic parser for them -- Hindsight's English-only
    ``DateparserQueryAnalyzer`` silently drops non-English temporal queries, and Cognee pins ``languages=["en"]``.
    What the engine does own is checking the result: aware, ordered, and consistent with when it was said
    (:mod:`~custom_memory_engine.semantic.temporal`).
    """

    start: str | None = None  # ISO-8601, timezone required
    end: str | None = None


@dataclass(frozen=True, slots=True)
class EntityMention:
    """One entity the claim is about, as it appeared plus what it refers to.

    ``label`` is the surface form in the source language; ``canonical`` is the model's proposal for what it refers to
    ("my boss" -> "Ahmed"), which is a *proposal* the cascaded resolver confirms or replaces. ``entity_id`` is filled
    only when the resolver matched an existing principal-scoped entity, so the write path can cite it as a typed
    foreign key (migration 19) instead of holding a string that looks like one.
    """

    label: str
    canonical: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ContractError("entity_label_required")


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    """One atomic claim, as the model reports it. Every field is validated; none is trusted.

    ``content`` is the canonical, self-contained statement ("Alice lives in Berlin"), in the source language or in a
    canonical language -- the deployment's choice, recorded per deployment. ``verbatim_quote`` is the exact substring
    of the source that licenses it, in the source language, and is what
    :func:`~custom_memory_engine.semantic.grounding.ground_claim` compares against the stored turn.
    """

    content: str
    verbatim_quote: str
    kind: ClaimKind = "claim"
    assertion_mode: AssertionMode = "asserted"
    cardinality: Cardinality = "single"
    um_facet: UmFacet | None = None
    semantic_slot: str | None = None
    entities: tuple[EntityMention, ...] = ()
    #: Which of ``entities`` the claim is *about*, when it is about one of them rather than about the speaker.
    #: ``None`` -- the common case for personal memory -- means the subject is the user (``subject_key='self'``). It is
    #: the surface form, matched against ``entities[].label`` exactly, because an index would be a second identity
    #: system on top of the one :mod:`~custom_memory_engine.semantic.entities` already resolves.
    subject_mention: str | None = None
    temporal: TemporalBounds = field(default_factory=TemporalBounds)
    #: Numbers, dates and identifiers the claim asserts ("10am", "€40", "v2.3.1"). Grounded deterministically: each
    #: must appear in the same source, which is Veracium's *specifics* check and the cheapest defence against a
    #: plausible-sounding fabrication.
    specifics: tuple[str, ...] = ()
    #: The model's own confidence, recorded but never used as an admission threshold: a self-reported number is not
    #: evidence, and the statistical verifier is what decides (``C-021``).
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ContractError("claim_content_required")
        if not isinstance(self.verbatim_quote, str) or not self.verbatim_quote.strip():
            raise ContractError("claim_quote_required")
        if self.kind not in CLAIM_KINDS:
            raise ContractError("claim_kind_invalid")
        if self.assertion_mode not in ASSERTION_MODES:
            raise ContractError("assertion_mode_invalid")
        if self.cardinality not in CARDINALITIES:
            raise ContractError("cardinality_invalid")
        if self.um_facet is not None and self.um_facet not in UM_FACETS:
            raise ContractError("um_facet_invalid")
        if self.semantic_slot is not None and (
            not isinstance(self.semantic_slot, str) or not self.semantic_slot.strip()
        ):
            raise ContractError("semantic_slot_invalid")
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ContractError("confidence_out_of_range")
        if not isinstance(self.entities, tuple) or not all(isinstance(item, EntityMention) for item in self.entities):
            raise ContractError("entities_invalid")
        if self.subject_mention is not None and (
            not isinstance(self.subject_mention, str)
            or not self.subject_mention.strip()
            or self.subject_mention not in {item.label for item in self.entities}
        ):
            # A subject that is not one of the stated mentions would be an entity the model invented on the side.
            raise ContractError("subject_mention_must_be_one_of_the_entities")
        if not isinstance(self.specifics, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.specifics
        ):
            raise ContractError("specifics_invalid")

    @property
    def single_valued(self) -> bool:
        """Whether this claim may supersede a prior belief.

        Only ``single``: an ``event`` or ``multi`` claim is a series, so two of them are two occurrences, not a
        contradiction. The engine still decides whether a *supersession* actually happens (a ``single`` claim about a
        different slot is additive); this is the candidate's own declaration, and
        :mod:`~custom_memory_engine.semantic.conflict` is what turns it into a decision.
        """
        return self.cardinality == "single"


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    """One unit of text to understand, with everything the model needs to resolve it.

    ``occurred_at`` is the turn's own instant: it is what "yesterday" is relative to, and the only anchor the model is
    given, because a model must never be asked to guess the present. ``language`` is optional and advisory -- the
    schema has no language column, and nothing in retrieval branches on one.
    """

    text: str
    occurred_at: str
    language: str | None = None
    scope_path: str | None = None
    #: Earlier turns of the same session, oldest first, so a pronoun can be resolved without a second round trip.
    context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ContractError("extraction_text_required")
        if not isinstance(self.occurred_at, str) or not self.occurred_at.strip():
            raise ContractError("extraction_occurred_at_required")
        if not isinstance(self.context, tuple) or not all(isinstance(item, str) for item in self.context):
            raise ContractError("extraction_context_invalid")


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """What one extraction produced: claims plus the diagnostics a deployment can log without logging content."""

    claims: tuple[ExtractedClaim, ...]
    #: The model and prompt identity, stored on every version it produces (``extractor_version``), so a re-extraction
    #: can be compared with the extraction it replaces.
    extractor_version: str
    language: str | None = None
    #: Free-form, content-free diagnostics (token counts, a stop reason). Never a value from the text.
    diagnostics: Mapping[str, int | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.claims, tuple) or not all(isinstance(item, ExtractedClaim) for item in self.claims):
            raise ContractError("extraction_claims_invalid")
        if not isinstance(self.extractor_version, str) or not self.extractor_version.strip():
            raise ContractError("extractor_version_required")


@dataclass(frozen=True, slots=True)
class CandidateBelief:
    """A claim paired with the exact source text it was extracted from, ready for deterministic validation.

    Kept separate from :class:`ExtractedClaim` because the pair -- claim *and* source -- is what grounding needs, and
    a claim that travels without its source must be unrepresentable rather than merely discouraged.
    """

    claim: ExtractedClaim
    source_text: str
    source_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.claim, ExtractedClaim):
            raise ContractError("candidate_claim_invalid")
        if not isinstance(self.source_text, str) or not self.source_text:
            raise ContractError("candidate_source_required")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ContractError("candidate_source_ref_required")


@dataclass(frozen=True, slots=True)
class AdjudicatedRelation:
    """A relation a *model* proposed between a candidate and one existing belief, plus the evidence it cited.

    The engine treats this as a proposal, never a verdict: :mod:`~custom_memory_engine.semantic.conflict` maps it to a
    deterministic write (supersession, reinforcement, a `conflict` association, or nothing) and the database's own
    invariants get the last word. ``rationale`` exists for an operator to review a decision; it is never rendered to a
    user and never stored as content.
    """

    existing_id: str
    relation: Relation
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.existing_id, str) or not self.existing_id.strip():
            raise ContractError("adjudicated_relation_requires_an_existing_id")
        if self.relation not in RELATIONS:
            raise ContractError("adjudicated_relation_invalid")


@runtime_checkable
class StructuredExtractor(Protocol):
    """The one model-shaped seam on the write path.

    Implementations live in the deployment (or in :mod:`custom_memory_engine.semantic.ingestor`'s tests), so no
    provider SDK, API key or retry policy enters the engine. The contract is narrow on purpose: one call per unit of
    text, structured out, no conversation state. An implementation that needs several calls may make them -- the
    engine measures the wall time and the tokens it is told about, not the number of round trips.

    **It must be idempotent for the same request.** Re-extraction is the engine's recovery path, and a
    non-deterministic extractor makes "the same turn produced different memories" unanswerable.

    Asynchronous because every real implementation is: an HTTP round trip to a model provider, or a local inference in
    a worker thread. A synchronous protocol would force each deployment to hide that behind ``run_in_executor``, and
    the one that forgot would block the event loop for the whole ingestion.
    """

    async def extract(self, request: ExtractionRequest) -> ExtractionResult: ...


@runtime_checkable
class RelationAdjudicator(Protocol):
    """The second model-shaped seam: classify one candidate against the existing beliefs recall found.

    Given a claim and a short list of candidates (``(id, content)``, already scoped and already current), return one
    :class:`AdjudicatedRelation` per candidate that is plausibly related, and nothing for the rest. An empty list is a
    valid answer and the common one.

    It is asked *blind* to which candidate the recall favoured -- the list carries no scores and no order beyond the
    stable one recall produced -- because an adjudicator shown a preferred answer tends to confirm it, which is
    precisely the failure Quipu's isolated verifier exists to avoid.
    """

    async def adjudicate(
        self, claim: ExtractedClaim, existing: Sequence[tuple[str, str]], *, language: str | None = None
    ) -> Sequence[AdjudicatedRelation]: ...


@runtime_checkable
class Embedder(Protocol):
    """Turn text into a dense vector, and name the model that did.

    Separate from the extractor because the two have different cost profiles and different failure modes: embedding is
    cheap, hot-path and must be available on every write; extraction is expensive, asynchronous and may be retried.
    The dimension is the caller's to keep consistent with ``hybrid.EMBEDDING_SLOTS``; the engine refuses a width it
    cannot index rather than storing a vector nothing will ever search.
    """

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


__all__ = [
    "ASSERTION_MODES",
    "CARDINALITIES",
    "CLAIM_KINDS",
    "RELATIONS",
    "UM_FACETS",
    "AdjudicatedRelation",
    "AssertionMode",
    "CandidateBelief",
    "Cardinality",
    "ClaimKind",
    "ContractError",
    "Embedder",
    "EntityMention",
    "ExtractedClaim",
    "ExtractionRequest",
    "ExtractionResult",
    "Relation",
    "RelationAdjudicator",
    "StructuredExtractor",
    "TemporalBounds",
    "UmFacet",
]
