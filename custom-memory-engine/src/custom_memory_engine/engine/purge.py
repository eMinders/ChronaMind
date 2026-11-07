"""Working-memory purge: the purged-evidence rule and its effect (E-026, ``API_V2.md`` 4.7, ``V2-D10``).

``purge_working(ctx, scope)`` resets a workspace: it deletes the ``retention='working'`` episodes of one scope. Two
documents meet here and neither fixes what happens to an artifact whose evidence is such an episode:

* ``ARCHITECTURE_V2.md`` section 1: "a workspace reset may purge working-scope episodes, and that purge is recorded as
  a scope event, **not a deletion of knowledge**", and working memory is "still evidence-bearing";
* ``DELETION_ORDER.md`` / ``V2-D09``: a *deletion* removes or re-derives everything derived from what it deletes.

Rule **PW-1** (chosen by E-026, recorded as finding ``E-026-F1`` on the W2 tracker), the one this module implements:

    A purge physically deletes every ``kind='episode'``, ``retention='working'`` row whose scope lies in the purged
    scope's subtree, **except** an episode that another surviving row cites as evidence (it is the source of a
    ``memory_derivation``, the lift evidence of a ``negative_constraint``, or the compared version of a
    ``memory_conflict``, or an endpoint of a ``contradicts`` association that opens a contradiction). Such an
    *evidence-bearing* episode is retained exactly as it is: neither promoted nor demoted. No other row is changed.

Why: removing an evidence-bearing episode can only be done in two ways, and each breaks a guarantee. Following the
deletion closure deletes the derived artifact, which makes the purge a deletion of knowledge (``V2-D10``, ``I10``).
Dropping the derivation pointer instead leaves an ``inferred`` row with no derivation (``I1'``) and, worse, lets a
later lineage deletion of that episode's source miss the inference built on it (``V2-D09``, ``I8``). Retaining the
episode breaks neither. The consequences are therefore testable as stated:

1. every artifact answerable before a purge is answerable after it, with an identical provenance record;
2. a purged episode is *absent* afterwards, not a tombstone: no read mode returns it, and an unknown id and a purged
   one are the same answer (``THREAT_MODEL.md`` T-11);
3. the purge is recorded as one content-free scope event (an ``audit_event`` of type ``working_purged``) carrying
   counts only;
4. a second purge of the same scope purges nothing (a retained episode stays retained while it is cited).

A grounded claim never points at an episode row: ``assert`` grounds it on the *source* the episode recorded (a turn),
which a purge does not touch, so purging that episode leaves the claim and its evidence intact by construction.

The rule is the pure function :func:`plan_purge`; :func:`purge_working` is the database effect around it.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.scope.paths import parse_scope
from custom_memory_engine.security.audit_guard import content_free_details

#: The scope event a purge records (``ARCHITECTURE_V2.md`` section 1): an ``audit_event``, not a new table.
PURGE_EVENT = "working_purged"

#: Every column that cites a ``memory_version`` row as evidence for another row, as ``(table, cited, citing)``. None of
#: their foreign keys cascades to the citing row. The derivation and conflict keys are restrictive, so a plan that
#: wrongly purged a cited episode fails at the database; the lift key is ``SET NULL`` and its guard then withdraws the
#: lift, which would silently re-activate a user's rejection (``I16``). Both are changes to knowledge, so both retain.
EVIDENCE_REFERENCES: tuple[tuple[str, str, str], ...] = (
    ("memory_derivation", "source_version_id", "derived_version_id"),
    ("negative_constraint", "lift_evidence_version_id", "id"),
    ("memory_conflict", "existing_version_id", "candidate_version_id"),
)

#: The origins whose ``contradicts`` edge routes its grounded endpoints to ``conflicts`` (``V2-D48`` C-P4, the same
#: set ``api.read_handlers`` reads). An association cascades with its endpoint, so purging an episode at one end of
#: such an edge would silently move the durable artifact at the other end back to ``facts``: the episode is evidence of
#: the contradiction and is retained. Every other association is relatedness, not evidence, and goes with the episode.
CONTRADICTION_ORIGINS = ("grounding_policy", "tier_c")


@dataclass(frozen=True, slots=True)
class PurgePlan:
    """What a purge does: the episodes it deletes and the evidence-bearing ones it keeps."""

    purged: tuple[str, ...]
    retained_as_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PurgeReceipt:
    """Content-free outcome of a purge: counts and the belief time only, never an id, a path or content."""

    purged_episodes: int
    retained_as_evidence: int
    recorded_at: datetime


def plan_purge(candidates: Sequence[str], citations: Iterable[tuple[str, str]]) -> PurgePlan:
    """Rule PW-1: purge every candidate that no surviving row cites as evidence; retain the rest.

    ``candidates`` are the working episodes in the purged scope. ``citations`` are ``(cited, citing)`` pairs: the row
    ``citing`` uses the row ``cited`` as evidence. A citation counts only while its citing row survives, so the plan is
    the fixpoint: a candidate cited only by another purged candidate is purged with it.

    This is the one place that decides what a purge does to an artifact whose evidence is a working episode: the
    artifact is never touched, and its evidence is kept (``V2-D10``: a purge never removes answerability).
    """
    pending = list(dict.fromkeys(candidates))
    purged = set(pending)
    cites: dict[str, set[str]] = {}  # citing -> what it cites, to propagate a retention backwards
    retained: list[str] = []
    for cited, citing in citations:
        if cited == citing or cited not in purged:
            continue
        cites.setdefault(citing, set()).add(cited)
        if citing not in purged:
            retained.append(cited)  # cited by a row the purge does not touch
    # Worklist: a retained candidate keeps everything it cites (linear in the citations, not a rescan per pass).
    while retained:
        episode = retained.pop()
        if episode in purged:
            purged.discard(episode)
            retained.extend(cites.get(episode, ()))
    return PurgePlan(
        purged=tuple(episode for episode in pending if episode in purged),
        retained_as_evidence=tuple(episode for episode in pending if episode not in purged),
    )


async def _verify(connection: AsyncConnection, user_id: str) -> None:
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("purge_user_context_mismatch")
        # The deletion lock (engine.deletion): a purge and a lineage deletion of one user never interleave.
        await cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (user_id,))


async def purge_working(
    connection: AsyncConnection, user_id: str, scope: str, *, schema_name: str = "cme_engine"
) -> PurgeReceipt:
    """Apply rule PW-1 to the working episodes of ``scope``'s subtree, in the caller's open transaction.

    Guarantee (``V2-D10``, ``I10``): the purge never removes answerability. It deletes only episodes that
    :func:`plan_purge` releases; every other row of every table is left exactly as it was.

    The candidates are locked ``FOR UPDATE`` before their citations are read. A concurrent write that would cite one
    of them (an ``infer`` naming it as a source) either commits first, and its citation then retains the episode, or
    waits for the purge and then finds its source gone. Nothing is marked or deferred: the purge, its scope event and
    the receipt are one transaction, so no intermediate state is observable.
    """
    target = parse_scope(scope)
    await _verify(connection, user_id)
    versions = sql.Identifier(schema_name, "memory_version")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id FROM {v} WHERE user_id = %s AND kind = 'episode' AND retention = 'working' "
                "AND pg_catalog.starts_with(scope_path, %s) ORDER BY id FOR UPDATE"
            ).format(v=versions),
            (user_id, target.text),
        )
        candidates = [str(row[0]) for row in await cursor.fetchall()]
        citations: list[tuple[str, str]] = []
        if candidates:
            for table, cited, citing in EVIDENCE_REFERENCES:
                await cursor.execute(
                    sql.SQL("SELECT {cited}, {citing} FROM {t} WHERE user_id = %s AND {cited} = ANY(%s)").format(
                        cited=sql.Identifier(cited),
                        citing=sql.Identifier(citing),
                        t=sql.Identifier(schema_name, table),
                    ),
                    (user_id, candidates),
                )
                citations.extend((str(row[0]), str(row[1])) for row in await cursor.fetchall())
            await cursor.execute(
                sql.SQL(
                    "SELECT from_id, to_id FROM {a} WHERE user_id = %s AND type = 'contradicts' "
                    "AND origin = ANY(%s) AND (from_id = ANY(%s) OR to_id = ANY(%s))"
                ).format(a=sql.Identifier(schema_name, "association")),
                (user_id, list(CONTRADICTION_ORIGINS), candidates, candidates),
            )
            for from_id, to_id in await cursor.fetchall():
                citations.extend(((str(from_id), str(to_id)), (str(to_id), str(from_id))))
        plan = plan_purge(candidates, citations)
        if plan.purged:
            # Evidence rows, embeddings and the remaining associations (relatedness) go with the row by cascade.
            await cursor.execute(
                sql.SQL("DELETE FROM {v} WHERE user_id = %s AND id = ANY(%s)").format(v=versions),
                (user_id, list(plan.purged)),
            )
        # the engine clock, as every other receipt reads it (api.runtime.now)
        await cursor.execute(sql.SQL("SELECT {}()").format(sql.Identifier(schema_name, "clock_now")))
        row = await cursor.fetchone()
        assert row is not None and isinstance(row[0], datetime)
        recorded_at = row[0]
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                "VALUES (%s, %s, %s, %s, %s, %s)"
            ).format(a=sql.Identifier(schema_name, "audit_event")),
            (
                f"audit-{uuid4().hex}",
                user_id,
                uuid4().hex,
                PURGE_EVENT,
                recorded_at,
                Jsonb(
                    content_free_details(
                        {"purged_episodes": len(plan.purged), "retained_as_evidence": len(plan.retained_as_evidence)}
                    )
                ),
            ),
        )
    return PurgeReceipt(len(plan.purged), len(plan.retained_as_evidence), recorded_at)


__all__ = [
    "CONTRADICTION_ORIGINS",
    "EVIDENCE_REFERENCES",
    "PURGE_EVENT",
    "PurgePlan",
    "PurgeReceipt",
    "plan_purge",
    "purge_working",
]
