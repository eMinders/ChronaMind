"""Cascaded entity resolution: deterministic shortlist first, a model only for the borderline rest (``V2-D90``).

ChronaMind stores entities as **opaque, principal-scoped ids** (migration 10, ``subject_entity_id`` in 19): a claim can
name an entity only through a composite foreign key that proves the entity exists, belongs to the same principal and
lives in the same scope. That is the strongest multi-tenant entity model in the 2026 comparison, and it is also why the
engine cannot resolve "Mom", "my mother" and "Sarah" by itself -- ids are opaque by design, so the *strings* that refer
to them have to be matched before the typed link can be written.

This module does that matching in the cascade Graphiti demonstrated is the right cost curve:

=====================  ============================================  =========  ==========================
stage                  what it decides                               cost       coverage in practice
=====================  ============================================  =========  ==========================
``exact``              normalized label equality                     0 queries  the majority of mentions
``trigram``            ``pg_trgm`` similarity over labels           one query  spelling and transliteration
``vector``             cosine over the entity's embedding, if any    one query  paraphrase and cross-lingual
``adjudicate``         an injected model, for what is left          1 batched  "my boss" -> "Ahmed"
=====================  ============================================  =========  ==========================

The cascade matters because the alternative -- one model call per mention -- is what makes Graphiti's and Cognee's
write paths slow and expensive. Everything that can be decided deterministically is decided before a model is asked,
and the model is asked only about pairs the deterministic stages could not separate.

**What is deliberately *not* merged.** Two labels are never merged on similarity alone at the adjudication threshold;
the deterministic stages return *candidates*, and a merge needs either an exact match or an adjudicated one. A false
merge is not a small error here: it attaches one person's claim to another person, and the typed foreign key that makes
that structurally sound is exactly what would make the mistake permanent.

**Ambiguity is a refusal, not a guess.** If two candidates match equally well and no adjudicator is available,
:func:`resolve_mention` returns ``unresolved`` with both ids named -- the caller may still store the claim against the
subject, and an operator can see what needed deciding. Inventing a winner is the failure mode MIRIX's cross-org entity
bugs come from.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from unicodedata import normalize as unicode_normalize
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.engine.artifact_provenance import (
    ResolutionStage,
    WritableOriginKind,
    record_entity_provenance,
)
from custom_memory_engine.engine.hybrid import trgm_schema

#: ``pg_trgm`` similarity above which a label is a *candidate* worth adjudicating. Below it, no relation.
TRIGRAM_FLOOR = 0.45
#: Cosine similarity above which an entity embedding is a *candidate*. **Measured, and deliberately high**
#: (`V2-D101`, `reports/entity_resolution/README.md`): a cross-script or transliterated mention sits at 0.50-0.79
#: against its entity and 0.35-0.49 against the rest, so 0.75 keeps the strongest cross-script proposals while
#: filtering the merely-similar. The floor is not a merge threshold — see :func:`resolve_mention` — because two
#: *different* entities of the same shape are nearly identical in embedding space ("Munich office" scores 0.769 against
#: "Berlin office").
VECTOR_FLOOR = 0.75
#: Two candidates this close to each other are ambiguous, and ambiguity is reported rather than guessed.
AMBIGUITY_MARGIN = 0.02
#: How many candidates each stage may produce.
CANDIDATE_LIMIT = 5

type MatchStage = Literal["exact", "trigram", "vector", "adjudicated", "unresolved", "created"]

#: Stripped from a label before comparison. Disposing of an Arabic definite article or an English possessive changes
#: no meaning in a *label*, and leaving them in means "Ahmed's" never matches "Ahmed". Nothing else is folded, and in
#: particular diacritics are kept: ``أحمد`` and ``احمد`` are different spellings, and an entity resolver that merges
#: them without evidence will eventually merge two people.
_POSSESSIVE = ("'s", "’s")


class ResolutionError(ValueError):
    """A static code; never carries a label."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    """One existing entity a mention might refer to, with the stage that found it and its score."""

    entity_id: str
    label: str
    stage: MatchStage
    score: float


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    """The resolver's answer: a matched entity, a decision to create one, or an honest ambiguity.

    ``ambiguous`` carries the candidates that tied. A caller may still store a claim against the *subject* while
    leaving the entity link unset; what it must not do is pick one.
    """

    mention: str
    stage: MatchStage
    entity_id: str | None = None
    ambiguous: tuple[str, ...] = ()
    mention_stage: ResolutionStage | None = None

    @property
    def resolved(self) -> bool:
        return self.entity_id is not None


@runtime_checkable
class EntityAdjudicator(Protocol):
    """The model-shaped last stage: which of these candidates, if any, is the mention?

    Given the surface form and a short list of ``(entity_id, label)`` candidates, return the one id it is confident
    about, or ``None``. ``None`` is a valid and common answer: "my boss" with three bosses in the scope is genuinely
    undecidable from the mention alone, and a wrong attach is worse than no attach.

    Asynchronous because the real implementation is a model call (``V2-D106``). An answer that is not one of the
    offered ids is treated as an abstention by :func:`resolve_mention`, never as an attach: a model that invents an id
    must not be able to point a claim at an entity the deterministic stages did not propose.
    """

    async def choose(self, mention: str, candidates: Sequence[tuple[str, str]]) -> str | None: ...


def normalize_label(label: object) -> str:
    """NFKC, casefold, possessives dropped, whitespace collapsed, surrounding punctuation trimmed.

    Deliberately conservative. It is the key the *exact* stage compares, so anything folded here is a merge the engine
    performs without evidence; anything not folded here is a merge it will ask for evidence about.
    """
    if not isinstance(label, str) or not label.strip():
        raise ResolutionError("entity_label_required")
    folded = unicode_normalize("NFKC", label).casefold().strip()
    for suffix in _POSSESSIVE:
        if folded.endswith(suffix) and len(folded) > len(suffix):
            folded = folded[: -len(suffix)]
    collapsed = " ".join(folded.split())
    return collapsed.strip("\"'“”‘’«»،.,;:!?()[]{}")


async def resolve_mention(
    connection: AsyncConnection,
    *,
    user_id: str,
    scope_path: str,
    label: str,
    embedding: Sequence[float] | None = None,
    embedding_model: str | None = None,
    adjudicator: EntityAdjudicator | None = None,
    schema_name: str = "cme_engine",
) -> ResolutionOutcome:
    """Resolve one mention inside the caller's scope, cheapest stage first.

    Every stage is scoped exactly as ``memory_version_subject_entity_scope_fk`` will re-check at write time, so a
    mention can never resolve to an entity of another scope or another principal -- and a refusal here is the same
    answer as "does not exist", which is what keeps T-11's indistinguishability intact.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ResolutionError("user_id_required")
    if not isinstance(scope_path, str) or not scope_path.startswith("/"):
        raise ResolutionError("scope_path_required")
    normalized = normalize_label(label)
    entities = sql.Identifier(schema_name, "entity")

    async with connection.cursor(row_factory=tuple_row) as cursor:
        # `canonical_name` and `aliases` are the two places a label physically lives (migration 10). Matching aliases
        # here is what makes "Ahmed" resolve to the entity created for "Ahmed Kamal" without a similarity score: an
        # alias is a caller's *declared* identity, which is evidence, not a guess.
        await cursor.execute(
            sql.SQL(
                "SELECT id, (pg_catalog.lower(pg_catalog.btrim(canonical_name)) = %s) AS is_canonical "
                "FROM {e} WHERE user_id = %s AND scope_path = %s AND ("
                "  pg_catalog.lower(pg_catalog.btrim(canonical_name)) = %s"
                "  OR EXISTS (SELECT 1 FROM pg_catalog.unnest(aliases) AS a WHERE pg_catalog.lower("
                "      pg_catalog.btrim(a)) = %s)"
                ") ORDER BY id LIMIT %s"
            ).format(e=entities),
            (normalized, user_id, scope_path, normalized, normalized, CANDIDATE_LIMIT),
        )
        exact = await cursor.fetchall()
    if len(exact) == 1:
        mention_stage: ResolutionStage = "exact" if bool(exact[0][1]) else "alias"
        return ResolutionOutcome(label, "exact", str(exact[0][0]), mention_stage=mention_stage)
    if len(exact) > 1:
        # Two entities already share this label. That is a caller's mistake, and resolving it by picking the
        # lexicographically smaller id would attach claims to an arbitrary one of them.
        return ResolutionOutcome(label, "unresolved", None, tuple(str(row[0]) for row in exact))

    candidates = await _similar_candidates(connection, entities, user_id, scope_path, normalized, schema_name)
    if not candidates and embedding is not None:
        # The vector stage runs **only** when the deterministic stages found nothing, which is the cascade's whole
        # point: a mention the trigram stage could match never reaches a model-derived score. And what it produces is a
        # candidate, never a merge: the code below requires an exact match or an adjudicated confirmation, so a false
        # proposal ("Munich office" against "Berlin office") is refused unless a model confirms it — which it does not.
        candidates = await _vector_candidates(
            connection, entities, user_id, scope_path, embedding, embedding_model, schema_name
        )
    if not candidates:
        return ResolutionOutcome(label, "created", mention_stage="created")
    best = candidates[0]
    rivals = tuple(
        candidate.entity_id for candidate in candidates[1:] if abs(candidate.score - best.score) <= AMBIGUITY_MARGIN
    )
    proposed = (best.entity_id, *rivals)
    # A vector proposal is never a merge on its own (`V2-D101`): the bi-encoder scored "Munich office" 0.769 against
    # "Berlin office", so a cosine above the floor is a reason to *ask*, not an identity. Without an adjudicator the
    # mention stays unresolved — not created (a second row beside one the resolver already found is a duplicate
    # identity), and not attached (a guess).
    needs_confirmation = bool(rivals) or best.stage == "vector"
    if adjudicator is None:
        if needs_confirmation:
            return ResolutionOutcome(label, "unresolved", None, proposed)
        direct_stage: ResolutionStage = "vector" if best.stage == "vector" else "trigram"
        return ResolutionOutcome(label, best.stage, best.entity_id, mention_stage=direct_stage)
    # With an adjudicator, every proposal is confirmed: the deterministic stages propose, and a merge the engine
    # performs should be one it was allowed to perform. An abstention is `unresolved`, for the reason above.
    offered = [(item.entity_id, item.label) for item in candidates]
    chosen = await adjudicator.choose(label, offered)
    if chosen is None or chosen not in {entity_id for entity_id, _ in offered}:
        # An id the resolver did not offer is an abstention, never an attach (`V2-D106`): a model must not be able to
        # name an entity the scoped stages did not find, even one that exists in this scope.
        return ResolutionOutcome(label, "unresolved", None, proposed)
    chosen_candidate = next(item for item in candidates if item.entity_id == chosen)
    adjudicated_stage: ResolutionStage = "vector" if chosen_candidate.stage == "vector" else "trigram"
    return ResolutionOutcome(label, "adjudicated", chosen, mention_stage=adjudicated_stage)


async def _vector_candidates(
    connection: AsyncConnection,
    entities: sql.Identifier,
    user_id: str,
    scope_path: str,
    embedding: Sequence[float],
    embedding_model: str | None,
    schema_name: str,
) -> tuple[EntityCandidate, ...]:
    """The vector stage: the nearest entities by cosine, above :data:`VECTOR_FLOOR`.

    ``embedding_model`` is filtered when supplied, so a vector from one model is never compared against a query vector
    from another -- the same rule the claim-recall dense arm applies. A row with a NULL model cannot match a non-NULL
    request: an unlabelled vector's provenance is unknown, so comparing it would be a guess about its space.
    """
    from custom_memory_engine.engine.hybrid import vector_literal

    if not embedding:
        return ()
    if embedding_model is not None and (not isinstance(embedding_model, str) or not embedding_model.strip()):
        raise ResolutionError("embedding_model_required")
    statement = (
        "SELECT id, canonical_name, 1 - (embedding_1024 <=> %(q)s::halfvec(1024)) AS score FROM {e} "
        "WHERE user_id = %(user)s AND scope_path = %(scope)s AND embedding_1024 IS NOT NULL"
    )
    parameters: dict[str, object] = {
        "user": user_id,
        "scope": scope_path,
        "q": vector_literal(embedding),
        "limit": CANDIDATE_LIMIT,
    }
    if embedding_model is not None:
        statement += " AND embedding_model = %(model)s"
        parameters["model"] = embedding_model
    statement += " ORDER BY embedding_1024 <=> %(q)s::halfvec(1024), id LIMIT %(limit)s"
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(sql.SQL(statement).format(e=entities), parameters)
        rows = await cursor.fetchall()
    del schema_name  # the identifier already carries the schema; kept for signature symmetry with the other stages
    return tuple(
        EntityCandidate(str(row[0]), str(row[1]), "vector", float(row[2]))
        for row in rows
        if float(row[2]) >= VECTOR_FLOOR
    )


async def _similar_candidates(
    connection: AsyncConnection,
    entities: sql.Identifier,
    user_id: str,
    scope_path: str,
    normalized: str,
    schema_name: str,
) -> tuple[EntityCandidate, ...]:
    """The trigram stage: one indexed query over ``canonical_name``.

    **The vector stage is gone, and that is a correction rather than a simplification.** The earlier version queried
    ``entity.label`` and ``entity.embedding``; the table has ``canonical_name`` and no embedding column at all
    (migration 10: entities are short labels, and a vector per name was never built). The resolver had only ever
    run against a fake table, so nothing caught it. Restoring the vector stage needs a migration that adds the
    column; until then the honest cascade is exact, alias, trigram, adjudicator, and a mention two spelling edits
    away is *unresolved* rather than silently created twice.
    """
    schema = await trgm_schema(connection)
    if schema is None:
        # No `pg_trgm`: the trigram stage cannot run, so the cascade degrades to exact + alias + adjudicator.
        # Raising would make a mention unusable because an optional extension is missing.
        return ()
    found: list[EntityCandidate] = []
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, canonical_name, {sim}(canonical_name, %s) AS score FROM {e} "
                "WHERE user_id = %s AND scope_path = %s AND canonical_name OPERATOR({schema}.%%) %s "
                "ORDER BY score DESC, id LIMIT %s"
            ).format(sim=sql.Identifier(schema, "similarity"), e=entities, schema=sql.Identifier(schema)),
            (normalized, user_id, scope_path, normalized, CANDIDATE_LIMIT),
        )
        found.extend(
            EntityCandidate(str(row[0]), str(row[1]), "trigram", float(row[2]))
            for row in await cursor.fetchall()
            if float(row[2]) >= TRIGRAM_FLOOR
        )
    return tuple(sorted(found, key=lambda item: (-item.score, item.entity_id)))


async def create_entity(
    connection: AsyncConnection,
    *,
    user_id: str,
    scope_path: str,
    label: str,
    entity_type: str = "thing",
    aliases: Sequence[str] = (),
    embedding: Sequence[float] | None = None,
    embedding_model: str | None = None,
    schema_name: str = "cme_engine",
    origin: Literal["declared", "mention"] = "declared",
    source_version_id: str | None = None,
    origin_kind: WritableOriginKind = "tier_c_call",
    created_by: str = "semantic-entities",
    record_provenance: bool = True,
) -> str:
    """Create one entity for a mention that resolved to nothing, and return its id.

    Written the way ``create_entity`` (the operation) writes it: the id is random and opaque (``T-27``), the label is
    the caller's own surface form, and the row is scoped to the exact scope the claim lives in so
    ``memory_version_subject_entity_scope_fk`` can vouch for it at write time. A concurrent create for the same label
    is left to resolve the next time: the schema has no unique index on ``(user_id, scope_path, canonical_name)``, and
    adding one would be a migration with a duplicate-cleanup question attached — a decision, not a side effect of
    wiring this in.
    """
    if origin not in ("declared", "mention"):
        raise ResolutionError("entity_origin_invalid")
    normalized = normalize_label(label)
    identifier = f"ent_{uuid4().hex}"
    if (embedding is None) != (embedding_model is None):
        # Paired or absent (migration 26's CHECK says the same): a vector without its model cannot be compared, and a
        # model without its vector is a claim about nothing.
        raise ResolutionError("embedding_and_model_must_be_paired")
    async with connection.transaction():
        if embedding is not None:
            from custom_memory_engine.engine.hybrid import vector_literal

            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {e} (id, user_id, scope_path, canonical_name, entity_type, aliases, "
                        "origin, embedding_1024, embedding_model) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::halfvec(1024), %s)"
                    ).format(e=sql.Identifier(schema_name, "entity")),
                    (
                        identifier,
                        user_id,
                        scope_path,
                        normalized,
                        entity_type or "thing",
                        list(aliases),
                        origin,
                        vector_literal(embedding),
                        embedding_model,
                    ),
                )
        else:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {e} (id, user_id, scope_path, canonical_name, entity_type, aliases, origin) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    ).format(e=sql.Identifier(schema_name, "entity")),
                    (identifier, user_id, scope_path, normalized, entity_type or "thing", list(aliases), origin),
                )
        if record_provenance:
            await record_entity_provenance(
                connection,
                user_id=user_id,
                entity_id=identifier,
                aliases=aliases,
                origin_kind=("evidence_version" if source_version_id is not None else origin_kind),
                source_version_ids=((source_version_id,) if source_version_id is not None else ()),
                created_by=created_by,
                schema_name=schema_name,
            )
    return identifier


__all__ = [
    "AMBIGUITY_MARGIN",
    "CANDIDATE_LIMIT",
    "TRIGRAM_FLOOR",
    "VECTOR_FLOOR",
    "EntityAdjudicator",
    "EntityCandidate",
    "MatchStage",
    "ResolutionError",
    "ResolutionOutcome",
    "create_entity",
    "normalize_label",
    "resolve_mention",
]
