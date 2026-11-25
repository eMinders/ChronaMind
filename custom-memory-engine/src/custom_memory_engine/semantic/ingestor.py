"""``SemanticIngestor``: the fast/slow lane that turns a turn into governed claims (``V2-D91``).

The engine already had the hard half -- provenance, bi-temporal supersession, RLS, channel separation, deletion
closure -- and no way in for un-structured text: ``api.ingest_handlers`` accepts pre-extracted claims plus a verbatim
span, and every consumer that wanted to feed it raw conversation had to reinvent the extraction, the grounding and the
conflict classification itself. In practice that means each consumer either reimplements governance badly or bypasses
it.

This module is the missing front door, and it is built as the **two lanes** MemOS and Honcho converge on, because the
latency budget and the correctness budget are not the same budget:

**Fast lane** (synchronous, milliseconds, no model). The turn is persisted and its episode is committed immediately
through the same write path every other artifact uses. From that instant the turn is durable, searchable by the
language-agnostic lexical arms, and inside the deletion boundary -- so a user who says something and immediately asks
about it is answered, and a user who deletes it has it actually deleted.

**Slow lane** (synchronous in this method, asynchronous in a deployment). One structured extraction per unit of text,
then per claim: deterministic grounding, the authority ladder, the three-stage conflict resolution, and only then the
governed write. Every failure mode is a *typed refusal with a reason recorded*, never a silent drop:
``grounding_failed``, ``authority_quarantine``, ``adjudicated_contradiction``. A claim that is refused leaves a row in
``memory_conflict`` (or a `conflict` association), which is what makes a model's mistake visible instead of invisible.

**Why the lanes are one method with a policy, not two code paths.** A deployment chooses how much of the slow lane runs
per call (``extract=False`` for a pure fast write, ``adjudicate=False`` for grounding without a second model) and every
combination is a *supported* configuration, so an ablation measures the same code the product runs (``RL-032``). Two
code paths would let the measured system diverge from the shipped one, which is the defect the whole phase exists to
remove.

**What is never done here.** No embeddings are computed, no model is called, and no table is written directly: the
caller injects an :class:`~custom_memory_engine.semantic.contracts.Embedder`, a
:class:`~custom_memory_engine.semantic.contracts.StructuredExtractor` and a
:class:`~custom_memory_engine.semantic.contracts.RelationAdjudicator`, and every row goes through
``engine.ingest``/``engine.transactions`` exactly as ``assert`` does. That is what keeps RLS, the GiST exclusion and
the provenance trigger in charge.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from typing import Literal, Protocol, runtime_checkable

import structlog
from psycopg import AsyncConnection, sql

from custom_memory_engine.clock import ClockTick, allocate_clock
from custom_memory_engine.engine.gate import GateDecision, record_decision
from custom_memory_engine.engine.ingest import (
    DEFAULT_EMBEDDING_MODEL,
    TurnInput,
    apply_candidate,
    persist_turn,
)
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput, stage_candidate
from custom_memory_engine.semantic import authority, conflict, entities, grounding, slots, temporal
from custom_memory_engine.semantic.contracts import (
    ClaimKind,
    Embedder,
    ExtractedClaim,
    ExtractionRequest,
    ExtractionResult,
    RelationAdjudicator,
    StructuredExtractor,
)

logger = structlog.get_logger(__name__)

#: How a claim fared. ``observed`` means the fast lane stored the turn; the rest describe the slow lane's verdict.
type Outcome = Literal["observed", "written", "reinforced", "superseded", "conflict", "quarantined", "rejected"]


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """One claim's fate, with the static reason a caller can act on and an operator can audit."""

    outcome: Outcome
    reason_code: str
    version_id: str | None = None
    existing_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """Everything one ingestion did, in order: the fast lane's receipt plus one outcome per claim."""

    turn_id: str
    episode_id: str | None
    claims: tuple[ClaimOutcome, ...] = ()
    extractor_version: str = ""
    #: Content-free diagnostics, safe to log: counts, token totals, the model's identity. Never a value from the text.
    diagnostics: dict[str, int | str] = field(default_factory=dict)

    @property
    def written(self) -> tuple[ClaimOutcome, ...]:
        return tuple(item for item in self.claims if item.outcome in ("written", "superseded", "reinforced"))


@runtime_checkable
class EpisodeWriter(Protocol):
    """The fast lane's write, injected so this module owns no SQL and no transaction of its own.

    A deployment passes the engine's own ``ingest_episode`` behaviour; a test passes a recorder. The signature is the
    turn plus its scope, and the answer is the persisted episode's id (``None`` when the profile forbids episodes).
    """

    async def __call__(
        self, connection: AsyncConnection, *, user_id: str, scope_path: str, turn: TurnInput, span: str
    ) -> str | None: ...


class IngestionError(ValueError):
    """A static code; never carries the turn's text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def ingest_turn(
    connection: AsyncConnection,
    *,
    user_id: str,
    scope_path: str,
    turn: TurnInput,
    write_episode: EpisodeWriter,
    extractor: StructuredExtractor | None = None,
    embedder: Embedder | None = None,
    adjudicator: RelationAdjudicator | None = None,
    entity_adjudicator: entities.EntityAdjudicator | None = None,
    schema_name: str = "cme_engine",
    context: Sequence[str] = (),
    language: str | None = None,
    similarity_floor: float | None = None,
) -> IngestionReport:
    """Persist ``turn`` immediately, then understand it. The caller owns the transaction and ``app.user_id``.

    ``extractor=None`` is the pure fast lane: the turn is recorded and nothing else happens. That is the configuration
    a latency-critical deployment runs on the request path, enqueueing the slow lane separately with this same function
    called again on a background worker (the turn insert is idempotent, so a retry is safe).
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise IngestionError("user_id_required")
    if not isinstance(scope_path, str) or not scope_path.startswith(f"/u/{user_id}/"):
        # The same rule the write path enforces, checked before any statement so the failure is a typed code.
        raise IngestionError("scope_outside_the_callers_namespace")

    # --- fast lane: the verbatim turn is durable before anything is understood ----------------------------------
    await persist_turn(connection, turn, schema_name=schema_name)
    episode_id = await write_episode(connection, user_id=user_id, scope_path=scope_path, turn=turn, span=turn.content)
    report = IngestionReport(turn_id=turn.turn_id, episode_id=episode_id)
    if extractor is None:
        return report

    # --- slow lane: understand, validate, resolve, write ----------------------------------------------------
    request = ExtractionRequest(
        text=turn.content,
        occurred_at=turn.occurred_at.astimezone(UTC).isoformat(),
        language=language,
        scope_path=scope_path,
        context=tuple(context),
    )
    result: ExtractionResult = await extractor.extract(request)
    return await apply_extraction(
        connection,
        user_id=user_id,
        scope_path=scope_path,
        turn=turn,
        episode_id=episode_id,
        extraction=result,
        embedder=embedder,
        adjudicator=adjudicator,
        entity_adjudicator=entity_adjudicator,
        schema_name=schema_name,
        language=language,
        similarity_floor=similarity_floor,
    )


async def apply_extraction(
    connection: AsyncConnection,
    *,
    user_id: str,
    scope_path: str,
    turn: TurnInput,
    episode_id: str | None,
    extraction: ExtractionResult,
    embedder: Embedder | None = None,
    adjudicator: RelationAdjudicator | None = None,
    entity_adjudicator: entities.EntityAdjudicator | None = None,
    schema_name: str = "cme_engine",
    language: str | None = None,
    similarity_floor: float | None = None,
    clock_tick: ClockTick | None = None,
) -> IngestionReport:
    """Write one already-computed extraction.

    This is the seam the asynchronous lane needs (``E-047``). A worker must not hold a database transaction open
    across a network call to a model: the row locks, the clock watermark and the pooled connection would all be
    held for seconds, and a serialization retry would re-bill the model. So extraction happens outside the
    transaction, and so do the *judgements*: the worker passes the replay adapters of a
    :class:`~custom_memory_engine.semantic.judgements.AnswerSheet` prepared before the transaction opened
    (``V2-D106``), which answer from memory and never call a model. Passing live model adapters here is still
    possible — ``ingest_turn`` does, for in-process callers that own their latency — but it holds the tenant's clock
    for every call they make.
    """
    result = extraction
    # One belief instant for the whole transaction. `allocate_clock` allocates **once** per tenant transaction (the
    # watermark is transaction-local), so every claim this turn produces must share the tick it returns; allocating per
    # claim would raise `clock_already_allocated_in_transaction` on the second one. This is also the behaviour a turn
    # should have: several facts from one utterance are believed at one instant.
    # One tick per transaction: a caller that already allocated one (the async lane's `complete_one`, which allocates
    # before it invokes the writer) passes it in, because `allocate_clock` raises on a second allocation.
    tick = clock_tick if clock_tick is not None else await allocate_clock(connection, user_id, schema_name=schema_name)
    outcomes: list[ClaimOutcome] = []
    entity_counts: dict[str, int] = {}
    for claim in result.claims:
        outcome, claim_entities = await _ingest_claim(
            connection,
            user_id=user_id,
            scope_path=scope_path,
            turn=turn,
            claim=claim,
            embedder=embedder,
            adjudicator=adjudicator,
            entity_adjudicator=entity_adjudicator,
            clock_tick=tick,
            schema_name=schema_name,
            language=result.language or language,
            similarity_floor=similarity_floor,
        )
        outcomes.append(outcome)
        for stage, count in claim_entities.items():
            entity_counts[stage] = entity_counts.get(stage, 0) + count
    logger.info(
        "semantic_ingestion_completed",
        user_id=user_id,
        claims_extracted=len(result.claims),
        claims_written=sum(1 for item in outcomes if item.outcome == "written"),
        claims_refused=sum(1 for item in outcomes if item.outcome in ("rejected", "quarantined")),
    )
    return IngestionReport(
        turn_id=turn.turn_id,
        episode_id=episode_id,
        claims=tuple(outcomes),
        extractor_version=result.extractor_version,
        # The model's own diagnostics plus the entity-resolution counts: integers keyed by stage, so an operator sees
        # whether mentions are being created rather than matched, or ties keep appearing, without reading any text.
        diagnostics={**dict(result.diagnostics), **{f"entity_{k}": v for k, v in entity_counts.items()}},
    )


async def _ingest_claim(
    connection: AsyncConnection,
    *,
    user_id: str,
    scope_path: str,
    turn: TurnInput,
    claim: ExtractedClaim,
    embedder: Embedder | None,
    adjudicator: RelationAdjudicator | None,
    entity_adjudicator: entities.EntityAdjudicator | None,
    clock_tick: ClockTick,
    schema_name: str,
    language: str | None,
    similarity_floor: float | None,
) -> tuple[ClaimOutcome, dict[str, int]]:
    """One claim, end to end. Every refusal returns a typed outcome, never an exception to interpret.

    The second element is the entity-resolution tally (stage -> count), which the report carries so a deployment can
    see whether its mentions converge without logging a single label (``V2-D90``).
    """
    # 1. Grounding: the quote exists and the specifics appear in the same text. Deterministic, and before anything
    #    expensive or anything that writes.
    verdict = grounding.ground_text(claim.content, claim.verbatim_quote, ((turn.content, turn.role),))
    if not verdict.grounded:
        return ClaimOutcome("rejected", verdict.code or "grounding_failed"), {}

    # 2. Authority: a claim is as authoritative as its weakest support. A turn's role decides it, structurally.
    candidate_authority = authority.rank_for("turn", turn.role)

    # 3. Temporal bounds: validated against the turn's own instant, never parsed here.
    try:
        bounds = temporal.resolve_claim_bounds(
            claim.temporal, occurred_at=turn.occurred_at, assertion_mode=claim.assertion_mode
        )
    except temporal.TemporalError as error:
        return ClaimOutcome("rejected", error.code), {}

    # 4. The soft slot, normalized once so the exclusion key and the write agree.
    try:
        semantic_slot = slots.normalize_slot(claim.semantic_slot)
    except slots.SlotError as error:
        return ClaimOutcome("rejected", error.code), {}

    embedding: Sequence[float] | None = None
    mention_embeddings: dict[str, tuple[float, ...]] = {}
    if embedder is not None:
        # One batched call for the claim and every distinct mention label (`V2-D101`): the mention vector lets the
        # resolver propose a cross-script or transliterated identity, and a second round trip per mention would make
        # the write path slower than the model it calls.
        labels = list(dict.fromkeys(mention.label for mention in claim.entities))
        vectors = await embedder.embed([claim.content, *labels])
        embedding = tuple(vectors[0])
        mention_embeddings = {label: tuple(vector) for label, vector in zip(labels, vectors[1:], strict=True)}

    # 4b. Entity resolution (E-041): every mention is resolved to one identity, or created, or left unresolved when
    #     two candidates tie. This is what makes a later mention of the same thing land on the same row instead of
    #     minting a second one; an ambiguity is reported, never guessed, so two people called "Sara" do not merge.
    subject_key, subject_entity_id, entity_outcomes = await _resolve_entities(
        connection,
        user_id=user_id,
        scope_path=scope_path,
        claim=claim,
        schema_name=schema_name,
        embedder=embedder,
        mention_embeddings=mention_embeddings,
        adjudicator=entity_adjudicator,
    )

    # 5. Embedding, when the deployment has one. Computed before the conflict recall so the recall can use it.
    # 6. Three-stage conflict resolution: recall, adjudicate, decide.
    neighbours = await conflict.recall_neighbours(
        connection,
        user_id=user_id,
        subject_key=subject_key,
        scope_path=scope_path,
        embedding=embedding,
        embedding_model=embedder.model if (embedder is not None and embedding is not None) else None,
        content=claim.content,
        similarity_floor=similarity_floor,
        schema_name=schema_name,
    )
    proposals = await conflict.adjudicate(claim, neighbours, adjudicator, language=language)
    resolution = conflict.commit_strategy(claim, candidate_authority, proposals, neighbours)
    if resolution.strategy == "quarantine":
        return (
            ClaimOutcome("quarantined", resolution.reason_code, existing_version_id=resolution.existing_id),
            entity_outcomes,
        )

    # 7. The governed write. `single_valued` follows the claim's cardinality *and* the resolution: an adjudicated
    #    duplicate or supersession is single-valued by definition, and an `event` claim never is.
    single_valued = claim.single_valued or resolution.strategy in ("reinforce", "supersede")
    candidate = CandidateVersionInput(
        version_id=_new_id("v"),
        memory_key=_new_id("mk"),
        user_id=user_id,
        scope_path=scope_path,
        kind=_kind_for(claim.kind),
        subject_key=subject_key,
        subject_entity_id=subject_entity_id,
        predicate_key="statement",
        semantic_slot=semantic_slot,
        cardinality_hint=claim.cardinality,
        # The facet is what makes this artifact part of the evolving user model (`USER_MODEL.md` §2.1); before W3.5
        # the column was read by that view and written by nothing.
        um_facet=claim.um_facet,
        single_valued=single_valued,
        content=claim.content,
        valid_from=bounds.start or turn.occurred_at,
        valid_until=bounds.end,
        layer="semantic",
        valid_from_basis=bounds.basis,
        assertion_mode=claim.assertion_mode,
        confidence=claim.confidence,
        extractor_version="semantic-ingestor",
        evidence=(EvidenceInput(turn_id=turn.turn_id, evidence_span=claim.verbatim_quote),),
    )
    if resolution.strategy == "open_conflict" and resolution.existing_id is not None:
        # A contradiction is **staged**, not written active. Two overlapping single-valued actives are unrepresentable
        # (the GiST exclusion), and letting the gate supersede would destroy one side of a disagreement the engine
        # cannot adjudicate. `contested` is the lifecycle the schema already has for exactly this (migration 7), and
        # `channel_of` routes it to `conflicts`, so neither side is presented as a fact while the disagreement is open.
        # Both facts survive: the existing belief is untouched, and the new one waits for adjudication.
        await stage_candidate(connection, candidate, initial_status="conflict_pending", schema_name=schema_name)
        await record_decision(
            connection,
            user_id=user_id,
            candidate_version_id=candidate.version_id,
            decision=GateDecision("hold", resolution.reason_code, ("conflict",), "conflict_pending"),
            existing_version_id=resolution.existing_id,
            decided_by="semantic-ingestor",
            schema_name=schema_name,
        )
        await _open_conflict(
            connection,
            user_id=user_id,
            candidate_id=candidate.version_id,
            existing_id=resolution.existing_id,
            schema_name=schema_name,
        )
        return (
            ClaimOutcome(
                "conflict",
                resolution.reason_code,
                version_id=candidate.version_id,
                existing_version_id=resolution.existing_id,
            ),
            entity_outcomes,
        )

    applied = await apply_candidate(
        connection,
        candidate,
        evidence_turns=((turn.role, turn.content),),
        clock_tick=clock_tick,
        schema_name=schema_name,
        embedding=embedding,
        embedding_model=embedder.model if embedder is not None else DEFAULT_EMBEDDING_MODEL,
        decided_by="semantic-ingestor",
    )
    if applied.decision.decision != "accept":
        return ClaimOutcome("rejected", applied.decision.reason_code, version_id=candidate.version_id), entity_outcomes
    outcome: Outcome = {
        "reinforce": "reinforced",
        "supersede": "superseded",
        "insert": "written",
    }.get(resolution.strategy, "written")  # type: ignore[arg-type]
    return (
        ClaimOutcome(
            outcome,
            resolution.reason_code,
            version_id=(applied.committed.version_id if applied.committed is not None else None),
            existing_version_id=applied.existing_version_id,
        ),
        entity_outcomes,
    )


async def _open_conflict(
    connection: AsyncConnection, *, user_id: str, candidate_id: str, existing_id: str, schema_name: str
) -> None:
    """Record the contradiction as a ``contradicts`` association between the two beliefs.

    Written in the same shape ``api.handlers.link`` writes, so the association cap, the composite tenant foreign keys
    and ``grounded_endpoint_protected`` apply unchanged. ``origin = 'tier_c'`` is the honest value: the engine's
    semantic pipeline *adjudicated* the relation rather than a Tier B caller believing it, and it is the value the read
    path's ``open_contradiction`` rule accepts (``V2-D48`` C-P4), so the edge both records the disagreement and routes
    it to the `conflicts` channel.
    """
    from uuid import uuid4

    await connection.execute(
        sql.SQL(
            "INSERT INTO {a} (id, user_id, from_id, to_id, type, weight, created_by, origin) "
            "VALUES (%s, %s, %s, %s, 'contradicts', 1.0, %s, 'tier_c') "
            "ON CONFLICT (user_id, from_id, to_id, type) DO NOTHING"
        ).format(a=sql.Identifier(schema_name, "association")),
        (f"assoc_{uuid4().hex}", user_id, candidate_id, existing_id, user_id),
    )


async def _resolve_entities(
    connection: AsyncConnection,
    *,
    user_id: str,
    scope_path: str,
    claim: ExtractedClaim,
    schema_name: str,
    embedder: Embedder | None = None,
    mention_embeddings: Mapping[str, tuple[float, ...]] | None = None,
    adjudicator: entities.EntityAdjudicator | None = None,
) -> tuple[str, str | None, dict[str, int]]:
    """Resolve every mention, create the unknown ones, and decide the claim's subject.

    Three outcomes and what each means for the write:

    * **resolved** (exact, alias, trigram or adjudicated): the mention has an id. The claim's subject is that entity
      only if the extractor named it as the subject; otherwise the subject stays ``self``.
    * **created**: the mention is new, so an entity row is written in the claim's own scope. Creating eagerly is what
      makes the *second* mention cheap; it costs one row and no belief, and the row is inside the deletion closure.
    * **unresolved**: two or more candidates tied, or a vector proposal was not confirmed. Nothing is created and
      nothing is attached — a wrong attach is worse
      than no attach (``V2-D90``), and the count is reported so an operator can see it happening.

    The returned counts are content-free by construction (integers keyed by stage), so they can go in the report.
    """
    counts: dict[str, int] = {}
    subject_key, subject_entity_id = "self", None
    cache: dict[str, entities.ResolutionOutcome] = {}
    mention_embeddings = mention_embeddings or {}
    for mention in claim.entities:
        outcome = cache.get(mention.label)
        if outcome is None:
            outcome = await entities.resolve_mention(
                connection,
                user_id=user_id,
                scope_path=scope_path,
                label=mention.label,
                # the vector stage only runs when the deterministic stages found nothing (`V2-D101`), and it only
                # proposes: a merge still needs an exact match or an adjudicated confirmation
                embedding=mention_embeddings.get(mention.label),
                embedding_model=embedder.model if embedder is not None else None,
                # confirms a proposal, never invents one: an answer outside the offered set is an abstention
                adjudicator=adjudicator,
                schema_name=schema_name,
            )
            if outcome.stage == "created":
                created = await entities.create_entity(
                    connection,
                    user_id=user_id,
                    scope_path=scope_path,
                    label=mention.label,
                    entity_type=mention.entity_type or "thing",
                    # the new row carries its vector, so the *next* cross-script mention of it can be proposed
                    embedding=mention_embeddings.get(mention.label),
                    embedding_model=embedder.model if embedder is not None else None,
                    schema_name=schema_name,
                )
                outcome = entities.ResolutionOutcome(mention.label, "created", created)
            cache[mention.label] = outcome
        counts[outcome.stage] = counts.get(outcome.stage, 0) + 1
        if outcome.resolved and claim.subject_mention is not None and mention.label == claim.subject_mention:
            subject_key, subject_entity_id = str(outcome.entity_id), outcome.entity_id
        elif outcome.resolved and claim.subject_mention is None and mention.entity_id is not None:
            # an extractor that already knows the entity id (a re-extraction of a known mention) is authoritative for
            # the subject only when it also says this mention is the subject; otherwise keep `self`
            counts["extractor_supplied_id"] = counts.get("extractor_supplied_id", 0) + 1
    return subject_key, subject_entity_id, counts


def _kind_for(kind: ClaimKind) -> ClaimKind:
    return kind


def _new_id(prefix: str) -> str:
    """A fresh opaque id, prefixed so a log line can name the kind without naming the content."""
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex}"


__all__ = [
    "ClaimOutcome",
    "EpisodeWriter",
    "apply_extraction",
    "IngestionError",
    "IngestionReport",
    "Outcome",
    "ingest_turn",
]
