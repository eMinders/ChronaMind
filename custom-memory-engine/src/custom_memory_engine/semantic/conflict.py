"""Three-stage open-ended conflict resolution: recall, adjudicate, commit (``V2-D89``).

The defect this replaces is narrow and fatal. ``engine.gate.existing_relation`` compared
``(subject_key, predicate_key)`` exactly and then compared *content* for exact lowercased equality. Because V2
``assert`` wrote ``predicate_key = 'statement'`` and ``single_valued = False`` for every claim, the conflict gate never
even ran on the public API. So two paraphrases of the same fact ("I moved to Berlin" / "Living in Berlin now") were two
unrelated memories, a correction never superseded anything, and a genuine contradiction was never noticed.

The fix is the shape Quipu and Graphiti converged on independently, adapted so that the write still lands in
ChronaMind's own invariants:

1. **Recall** -- *deterministic, one statement.* Vector similarity over the multilingual slot (cosine, HNSW) or, when
   no embedder is available, trigram similarity, restricted to the caller's scope and to current beliefs, and filtered
   to single-valued rows that share the candidate's subject. Scores order the shortlist; they never decide.
2. **Adjudicate** -- *model, isolated.* The candidate and the shortlist go to an injected
   :class:`~custom_memory_engine.semantic.contracts.RelationAdjudicator`, which returns one relation per plausible
   neighbour. It sees no score and no order beyond the stable recall order, because an adjudicator shown a preferred
   answer tends to confirm it.
3. **Commit** -- *deterministic.* The relation maps to exactly one write: ``duplicate`` reinforces, ``update`` and
   ``correction`` supersede, ``contradiction`` opens a `conflict` association, ``additive``/``unrelated`` insert. The
   authority ladder can veto the first three (:mod:`~custom_memory_engine.semantic.authority`), and the GiST exclusion
   gets the last word whatever this module decided -- which is why a model can be wrong here without the ledger being
   wrong.

**Why the recall is over ``single_valued`` rows only.** A `multi` claim is a series; two of them are two occurrences,
not a contradiction, and recalling them would invite the adjudicator to "resolve" a non-conflict. The exclusion
constraint has the same scope, so the two agree by construction rather than by intention.

**Why the shortlist is capped.** Adjudication is the expensive call. Ten neighbours costs one batched model call and
covers the real case; a hundred costs ten and would find only noise, because a paraphrase that ranks 100th is not the
belief the user is correcting.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, LiteralString, cast

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.engine.hybrid import slot_for_dimension, trgm_schema, vector_literal
from custom_memory_engine.semantic.authority import Authority, may_supersede, version_authority
from custom_memory_engine.semantic.contracts import AdjudicatedRelation, ExtractedClaim, Relation, RelationAdjudicator

#: How many neighbours adjudication sees. Ten is one batched call and covers a correction of any recent belief.
RECALL_LIMIT = 10
#: Cosine similarity below which a neighbour is not worth adjudicating. 0.78 is the threshold the 2026 engines
#: converge on for paraphrase-equivalence in multilingual embedding space; it is a *recall* bound, not a decision,
#: so a false positive costs one extra pair in a model call and a false negative costs a missed supersession. Kept
#: configurable rather than hardcoded into SQL so a deployment can tune it.
DEFAULT_SIMILARITY_FLOOR = 0.78
#: ``pg_trgm`` similarity below which a lexical neighbour is not worth adjudicating. **A different scale, and it must
#: be a different number.** Trigram similarity of two sentences that differ by one content word sits near 0.5–0.7, so
#: the cosine floor applied to it would silently disable text recall -- the pre-W3.5 behaviour arriving through the
#: back door, which is exactly the defect `V2-D89` removes. 0.45 matches the entity resolver's own trigram floor.
TRIGRAM_SIMILARITY_FLOOR = 0.45

type CommitStrategy = Literal["reinforce", "supersede", "open_conflict", "insert", "quarantine"]


class ConflictError(ValueError):
    """A static code; never carries a claim or an id."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Neighbour:
    """One current belief the recall found, with the score that ranked it and its own authority."""

    version_id: str
    content: str
    similarity: float
    authority: Authority


@dataclass(frozen=True, slots=True)
class Resolution:
    """What the deterministic stage decided, ready for the write path to execute.

    ``strategy`` is the only thing the write path switches on; ``relation`` and ``existing_id`` are recorded with the
    decision so a reviewer can see the model's proposal beside the engine's answer.
    """

    strategy: CommitStrategy
    relation: Relation
    existing_id: str | None
    reason_code: str


def _slot(embedding: Sequence[float], model: str) -> tuple[str, str]:
    column, slot_type = slot_for_dimension(len(embedding))
    return column, slot_type


async def recall_neighbours(
    connection: AsyncConnection,
    *,
    user_id: str,
    subject_key: str,
    scope_path: str,
    embedding: Sequence[float] | None = None,
    embedding_model: str | None = None,
    content: str | None = None,
    limit: int = RECALL_LIMIT,
    similarity_floor: float | None = None,
    schema_name: str = "cme_engine",
) -> tuple[Neighbour, ...]:
    """Stage 1: the current single-valued beliefs that could be the one this claim is about.

    Two recall paths, one contract. With an embedding, cosine distance in the deployed slot (HNSW-indexed, migration
    23) -- multilingual by construction, so a claim written in Arabic is compared with a belief written in English.
    Without one, trigram similarity, which is exact but weaker: it finds the same words in a different order and
    nothing else. A deployment without an embedder therefore degrades to what a careful keyword search would find,
    and never to "no conflict detection at all".

    The result is ordered by descending similarity then id, so two identical calls produce identical shortlists and an
    adjudicator's answer is reproducible.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ConflictError("user_id_required")
    if not isinstance(scope_path, str) or not scope_path.startswith("/"):
        raise ConflictError("scope_path_required")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ConflictError("positive_recall_limit_required")
    if embedding is None and (content is None or not content.strip()):
        raise ConflictError("recall_requires_an_embedding_or_a_query_text")

    versions = sql.Identifier(schema_name, "memory_version")
    # Only current, only single-valued, only this subject: the same population `no_bitemporal_overlap` governs, so an
    # exhaustive recall finds no more than the constraint could hold.
    population = (
        "m.user_id = %(user_id)s AND m.subject_key = %(subject_key)s AND m.scope_path = %(scope_path)s "
        "AND m.single_valued AND m.status = 'active' AND m.recorded_range IS NOT NULL "
        "AND upper_inf(m.recorded_range)"
    )
    if embedding is not None:
        if not isinstance(embedding_model, str) or not embedding_model.strip():
            raise ConflictError("nonblank_embedding_model_required")
        column, slot_type = _slot(embedding, embedding_model)
        statement = sql.SQL(
            "SELECT m.id, m.content, 1 - (e.{slot} <=> %(embedding)s::{slot_type}({dimensions})) AS similarity "
            "FROM {versions} AS m "
            "JOIN {embeddings} AS e ON e.version_id = m.id AND e.user_id = m.user_id "
            "AND e.embedding_model = %(embedding_model)s AND e.{slot} IS NOT NULL "
            "WHERE {population} "
            "ORDER BY e.{slot} <=> %(embedding)s::{slot_type}({dimensions}), m.id LIMIT %(limit)s"
        ).format(
            versions=versions,
            embeddings=sql.Identifier(schema_name, "memory_embedding"),
            slot=sql.Identifier(column),
            slot_type=sql.SQL(cast(LiteralString, slot_type)),
            dimensions=sql.Literal(len(embedding)),
            population=sql.SQL(population),
        )
        parameters: dict[str, object] = {
            "user_id": user_id,
            "subject_key": subject_key,
            "scope_path": scope_path,
            "embedding_model": embedding_model,
            "embedding": vector_literal(embedding),
            "limit": limit,
        }
    else:
        assert content is not None
        # `similarity()` and `%` belong to whichever schema `pg_trgm` was installed in, so both are qualified from the
        # catalog rather than from a `search_path` assumption (see `engine.hybrid.trgm_schema`).
        extension = await trgm_schema(connection)
        if extension is None:
            # Migration 23 requires the extension, so its absence is a provisioning defect, not a reason to silently
            # stop detecting conflicts -- which would be the pre-W3.5 behaviour arriving through the back door.
            raise ConflictError("pg_trgm_required_for_text_recall")
        similarity = sql.SQL("{}.similarity").format(sql.Identifier(extension))
        statement = sql.SQL(
            "SELECT m.id, m.content, {similarity}(m.content, %(content)s) AS similarity "
            "FROM {versions} AS m WHERE {population} "
            # the `%` operator, spelled schema-qualified and with psycopg's placeholder escape
            "AND m.content OPERATOR({schema}.%%) %(content)s "
            "ORDER BY similarity DESC, m.id LIMIT %(limit)s"
        ).format(
            versions=versions,
            population=sql.SQL(population),
            similarity=similarity,
            schema=sql.Identifier(extension),
        )
        parameters = {
            "user_id": user_id,
            "subject_key": subject_key,
            "scope_path": scope_path,
            "content": content,
            "limit": limit,
        }

    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(statement, parameters)
        found = await cursor.fetchall()
        if not found:
            return ()
        ids = [str(row[0]) for row in found]
        evidence = await _evidence_of(connection, user_id, ids, schema_name)
    floor = (
        similarity_floor
        if similarity_floor is not None
        else (DEFAULT_SIMILARITY_FLOOR if embedding is not None else TRIGRAM_SIMILARITY_FLOOR)
    )
    neighbours = [
        Neighbour(
            version_id=str(version_id),
            content=str(row_content),
            similarity=float(similarity),
            authority=version_authority(evidence.get(str(version_id), ())),
        )
        for version_id, row_content, similarity in found
    ]
    return tuple(item for item in neighbours if item.similarity >= floor)


async def _evidence_of(
    connection: AsyncConnection, user_id: str, version_ids: Sequence[str], schema_name: str
) -> Mapping[str, tuple[tuple[str, str | None], ...]]:
    """Each version's ``(source_type, evidence_role)`` pairs, for the authority ladder.

    ``memory_evidence.authority_rank`` (migration 24) is the authoritative derivation and is read here when present;
    the raw columns are grouped instead so this query works against a schema rolled back below migration 24 as well,
    which the downgrade tests do.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT version_id, source_type, evidence_role FROM {e} WHERE user_id = %s AND version_id = ANY(%s)"
            ).format(e=sql.Identifier(schema_name, "memory_evidence")),
            (user_id, list(version_ids)),
        )
        grouped: dict[str, list[tuple[str, str | None]]] = {}
        for version_id, source_type, role in await cursor.fetchall():
            grouped.setdefault(str(version_id), []).append((str(source_type), None if role is None else str(role)))
    return {version_id: tuple(pairs) for version_id, pairs in grouped.items()}


async def adjudicate(
    claim: ExtractedClaim,
    neighbours: Sequence[Neighbour],
    adjudicator: RelationAdjudicator | None,
    *,
    language: str | None = None,
) -> tuple[AdjudicatedRelation, ...]:
    """Stage 2: ask the model what each neighbour is to this claim.

    No adjudicator means no semantic dedup -- the pre-W3.5 behaviour, kept reachable so an ablation can measure what
    the model is worth. It is *not* silently replaced by exact string equality: silence is honest, and a caller that
    wants the old cheap check asks for it explicitly by handling relations itself.
    """
    if adjudicator is None or not neighbours:
        return ()
    existing = tuple((neighbour.version_id, neighbour.content) for neighbour in neighbours)
    return tuple(await adjudicator.adjudicate(claim, existing, language=language))


def commit_strategy(
    claim: ExtractedClaim,
    candidate_authority: Authority,
    relations: Sequence[AdjudicatedRelation],
    neighbours: Sequence[Neighbour],
) -> Resolution:
    """Stage 3: the deterministic decision, from the model's proposals and the ladder.

    Precedence is fixed, and the order is the whole design:

    1. **A contradiction opens a conflict.** It is the only relation that must not be resolved silently, because the
       engine cannot know which side is right -- and presenting one as fact is the failure the channel router exists to
       prevent.
    2. **A duplicate reinforces.** Reinforcing is the cheapest correct answer and it is what keeps repeated statements
       from becoming an ever-growing list of near-copies.
    3. **A correction supersedes, an update supersedes.** Both say "this belief was wrong or has changed", which is
       what the bi-temporal close-then-insert exists to record honestly.
    4. **Everything else inserts.** ``additive``/``unrelated``, and *any* relation this module does not recognise,
       which is why the default is insert rather than a guess.
    5. **The ladder can veto 2–4.** A third-party claim may not supersede, reinforce or contradict-resolve a
       user-authored belief: it is quarantined, recorded, and routed to `conflicts`. Equal rank is allowed, because a
       user correcting themselves is the commonest supersession there is.

    A relation naming a version that recall did not return is ignored: the adjudicator may hallucinate an id, and
    honouring it would be a write to a belief the engine never showed it.
    """
    if candidate_authority not in (0, 1, 2, 3):
        raise ConflictError("candidate_authority_invalid")
    known = {neighbour.version_id: neighbour for neighbour in neighbours}
    by_relation: dict[str, AdjudicatedRelation] = {}
    for proposal in relations:
        neighbour = known.get(proposal.existing_id)
        if neighbour is None:
            continue
        by_relation.setdefault(proposal.relation, proposal)
    if "contradiction" in by_relation:
        proposal = by_relation["contradiction"]
        neighbour = known[proposal.existing_id]
        if may_supersede(candidate_authority, neighbour.authority).refused:
            return Resolution("quarantine", "contradiction", neighbour.version_id, "authority_quarantine")
        return Resolution("open_conflict", "contradiction", neighbour.version_id, "adjudicated_contradiction")
    for relation, strategy, reason in (
        ("duplicate", "reinforce", "adjudicated_duplicate"),
        ("correction", "supersede", "adjudicated_correction"),
        ("update", "supersede", "adjudicated_update"),
    ):
        if relation not in by_relation:
            continue
        proposal = by_relation[relation]
        neighbour = known[proposal.existing_id]
        decision = may_supersede(candidate_authority, neighbour.authority)
        if decision.refused:
            return Resolution("quarantine", proposal.relation, neighbour.version_id, decision.reason_code)
        return Resolution(strategy, proposal.relation, neighbour.version_id, reason)  # type: ignore[arg-type]
    return Resolution("insert", "additive", None, "no_relation_established")


__all__ = [
    "DEFAULT_SIMILARITY_FLOOR",
    "RECALL_LIMIT",
    "TRIGRAM_SIMILARITY_FLOOR",
    "CommitStrategy",
    "ConflictError",
    "Neighbour",
    "Resolution",
    "adjudicate",
    "commit_strategy",
    "recall_neighbours",
]
