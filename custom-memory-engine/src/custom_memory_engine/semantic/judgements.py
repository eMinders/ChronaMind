"""Every model judgement a write needs, asked **before** the write transaction opens (``V2-D106``).

**The defect this fixes.** ``apply_extraction`` was documented as "pure database work", and the slow lane's whole
design (``E-047``) rests on that: a worker must not hold a transaction open across a network call. But it received the
embedder and the relation adjudicator and called both *inside* the transaction, after ``allocate_clock`` had locked
the tenant's watermark row. With the measured extraction model at ~9 s per call, one adjudicated claim held that lock
for seconds and every other write for the same user — including the fast lane answering a live conversation — queued
behind it. Adding an entity adjudicator would have made it worse.

**The shape of the fix: ask, then write, and the write may only replay.**

1. *Embed* every text the write will embed — each claim's content and each distinct mention label. That needs no
   database, so it happens first, outside any transaction.
2. *Dry-run* the write in a transaction that is always rolled back, with **recording** adapters: they return no
   opinion and record exactly which questions the write asked — which mention against which candidate set, which claim
   against which neighbours. The dry run allocates the clock like the real write, so it asks the real questions, but
   it holds the lock for milliseconds, not for a model call.
3. *Answer* the recorded questions with the real models, outside any transaction.
4. *Write* with **replay** adapters that answer from the sheet. A question the sheet does not hold — because the state
   moved between the dry run and the write — gets an abstention, never a fresh call. Abstaining is the safe
   direction everywhere it is consumed: an unconfirmed entity stays unresolved, and an unjudged neighbour leaves the
   claim inserted.

Entity answers can change the subject, and the subject decides which neighbours a claim is compared with. So when the
first dry run asked an entity question, a second dry run asks the relation questions under the answered entities. When
it asked none (the common case), the first dry run's relation questions are already the right ones.

**What it costs.** Two or three short database passes instead of one long one. The extra passes run the deterministic
part of the write again, which the measurement in ``reports/benchmarks`` puts at tens of milliseconds — a trade of
milliseconds of database time for seconds of lock time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

import structlog

from custom_memory_engine.semantic.contracts import (
    AdjudicatedRelation,
    Embedder,
    ExtractedClaim,
    ExtractionResult,
    RelationAdjudicator,
)
from custom_memory_engine.semantic.entities import EntityAdjudicator

logger = structlog.get_logger(__name__)

type EntityKey = tuple[str, tuple[str, ...]]
type RelationKey = tuple[str, str]


class JudgementError(RuntimeError):
    """A static code. Raised when the write asks for a vector the sheet does not hold — a programming error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _entity_key(mention: str, candidates: Sequence[tuple[str, str]]) -> EntityKey:
    # The candidate *set*, ordered: the same mention against a different set is a different question, and an answer to
    # the old set must not be replayed against a new one.
    return mention, tuple(sorted(entity_id for entity_id, _ in candidates))


@dataclass
class AnswerSheet:
    """The questions a write asked and the answers the models gave. Content stays in process memory only."""

    model: str | None = None
    dimensions: int = 0
    vectors: dict[str, tuple[float, ...]] = field(default_factory=dict)
    entity_questions: dict[EntityKey, list[tuple[str, str]]] = field(default_factory=dict)
    entity_answers: dict[EntityKey, str | None] = field(default_factory=dict)
    relation_questions: dict[str, tuple[ExtractedClaim, dict[str, str], str | None]] = field(default_factory=dict)
    relation_answers: dict[RelationKey, AdjudicatedRelation] = field(default_factory=dict)
    #: How many judgements were asked and answered, for the worker's content-free log line.
    asked: dict[str, int] = field(default_factory=dict)

    # -- adapters the write is given --------------------------------------------------------------------------

    def embedder(self) -> Embedder | None:
        return None if self.model is None else _ReplayEmbedder(self)

    def recording_entities(self) -> EntityAdjudicator:
        return _RecordingEntities(self)

    def replay_entities(self) -> EntityAdjudicator:
        return _ReplayEntities(self)

    def recording_relations(self) -> RelationAdjudicator:
        return _RecordingRelations(self)

    def replay_relations(self) -> RelationAdjudicator:
        return _ReplayRelations(self)


@dataclass
class _ReplayEmbedder:
    sheet: AnswerSheet

    @property
    def model(self) -> str:
        assert self.sheet.model is not None
        return self.sheet.model

    @property
    def dimensions(self) -> int:
        return self.sheet.dimensions

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> Sequence[Sequence[float]]:
        del query
        missing = [text for text in texts if text not in self.sheet.vectors]
        if missing:
            raise JudgementError("vector_not_prepared")
        return [self.sheet.vectors[text] for text in texts]


@dataclass
class _RecordingEntities:
    sheet: AnswerSheet

    async def choose(self, mention: str, candidates: Sequence[tuple[str, str]]) -> str | None:
        self.sheet.entity_questions[_entity_key(mention, candidates)] = list(candidates)
        return None


@dataclass
class _ReplayEntities:
    sheet: AnswerSheet

    async def choose(self, mention: str, candidates: Sequence[tuple[str, str]]) -> str | None:
        return self.sheet.entity_answers.get(_entity_key(mention, candidates))


@dataclass
class _RecordingRelations:
    sheet: AnswerSheet

    async def adjudicate(
        self, claim: ExtractedClaim, existing: Sequence[tuple[str, str]], *, language: str | None = None
    ) -> Sequence[AdjudicatedRelation]:
        _, known, _ = self.sheet.relation_questions.get(claim.content, (claim, {}, language))
        self.sheet.relation_questions[claim.content] = (claim, {**known, **dict(existing)}, language)
        return ()


@dataclass
class _ReplayRelations:
    sheet: AnswerSheet

    async def adjudicate(
        self, claim: ExtractedClaim, existing: Sequence[tuple[str, str]], *, language: str | None = None
    ) -> Sequence[AdjudicatedRelation]:
        del language
        # Only neighbours that are still the ones the model judged: a neighbour that appeared since the dry run has
        # no answer and therefore no opinion, which `commit_strategy` treats as "insert".
        return tuple(
            answer
            for version_id, _ in existing
            if (answer := self.sheet.relation_answers.get((claim.content, version_id))) is not None
        )


type DryRun = Callable[[Embedder | None, RelationAdjudicator | None, EntityAdjudicator | None], Awaitable[None]]


async def prepare(
    extraction: ExtractionResult,
    dry_run: DryRun,
    *,
    embedder: Embedder | None,
    adjudicator: RelationAdjudicator | None,
    entity_adjudicator: EntityAdjudicator | None,
) -> AnswerSheet:
    """Ask every judgement the write will need. **Must be called with no transaction open.**

    ``dry_run`` runs the real write in a transaction that is rolled back, with the adapters it is given; the caller
    owns that transaction because only the caller knows its tenant context. This function never touches the database.
    """
    sheet = AnswerSheet(
        model=embedder.model if embedder is not None else None,
        dimensions=embedder.dimensions if embedder is not None else 0,
    )
    if embedder is not None:
        texts: list[str] = []
        for claim in extraction.claims:
            texts.append(claim.content)
            texts.extend(mention.label for mention in claim.entities)
        unique = list(dict.fromkeys(texts))
        if unique:
            vectors = await embedder.embed(unique)
            sheet.vectors = {text: tuple(vector) for text, vector in zip(unique, vectors, strict=True)}
        sheet.asked["vectors"] = len(unique)
    if adjudicator is None and entity_adjudicator is None:
        return sheet

    # Round 1: what does the write ask, before any entity is confirmed?
    await dry_run(
        sheet.embedder(),
        sheet.recording_relations() if adjudicator is not None else None,
        sheet.recording_entities() if entity_adjudicator is not None else None,
    )
    if entity_adjudicator is not None and sheet.entity_questions:
        for key, offered in sheet.entity_questions.items():
            sheet.entity_answers[key] = await entity_adjudicator.choose(key[0], offered)
        sheet.asked["entities"] = len(sheet.entity_questions)
        if adjudicator is not None:
            # Round 2: the answered entities may move a claim's subject, and with it the neighbours it is compared
            # against, so the relation questions are re-asked under the answers rather than reused.
            sheet.relation_questions.clear()
            await dry_run(sheet.embedder(), sheet.recording_relations(), sheet.replay_entities())
    if adjudicator is not None:
        for content, (claim, existing, language) in sheet.relation_questions.items():
            answers = await adjudicator.adjudicate(claim, tuple(existing.items()), language=language)
            for answer in answers:
                if answer.existing_id in existing:
                    sheet.relation_answers[(content, answer.existing_id)] = answer
        sheet.asked["relations"] = sum(len(existing) for _, existing, _ in sheet.relation_questions.values())
    logger.info("judgements_prepared", **sheet.asked)
    return sheet


def counts(sheet: AnswerSheet) -> Mapping[str, int]:
    """Content-free: how many of each judgement were asked."""
    return dict(sheet.asked)


__all__ = ["AnswerSheet", "JudgementError", "counts", "prepare"]
