"""Derived staleness: the closure hash, its cache, and the wiring that feeds ``StalenessInputs`` (E-038; ``V2-D24``).

``stale`` is **computed, never stored** (``ARCHITECTURE_V2.md`` 9.2). A stored flag can disagree with the provenance
graph and deletion is exactly what creates the disagreement, so there is no column for it and every read derives it:

    stale(a)  <=>  a is verified  and  any of the five causes of ``CHANNELS.md`` 2.3 holds

``engine.channels`` keeps the pure ``stale_of`` over explicit :class:`StalenessInputs`; this module is the only place
in ``src/`` that builds them (``tests/integration/test_staleness_db.py`` scans for any other construction site).

**What the closure hash covers.** ``closure_hash`` is a SHA-256 over the *transitive source set* of an artifact: for
every ancestor reachable through ``memory_derivation`` (any depth, cycle-safe), its id and the canonical content
digest (``grounding.sources.canonical_hash``, ``V2-D41``). The lines are sorted by id, so insertion order cannot
manufacture staleness. An artifact with no sources hashes the empty set (total, no exception).

**What it deliberately excludes, and why.** Lifecycle, status and ``recorded_range`` (a retirement is its own cause,
``source_retired``, and must not also be hash noise); ``reinforcement_count``, importance, retention, scope,
embeddings and the extractor version (none changes what the artifact was derived *from*); the ``derivation_type`` of
an edge. Belief content is immutable in place (``guard_update``), so the content digest changes only when an ancestor
row is replaced or removed.

**Cache and invalidation contract (``DELETION_ORDER.md`` 5 row F6).** Computing the hash walks the whole ancestry, so
it is cached, keyed ``(schema, user_id, artifact_id, deletion_token)``. The token is ``epoch:request_id`` for the
user's most recent ``deletion_request``, read in the **same transaction as the read**.
``delete_memory`` allocates the next epoch inside its own transaction, so the effect of a deletion is visible to the
very next read on that connection, and a cache in another process simply never reads the old-token entry.

The ``request_id`` is part of the key because the epoch integer alone is **not** unique over time. It is allocated as
``MAX(deletion_epoch) + 1``, so a deleted-and-rolled-back request puts its number back in the pool and the next real
deletion reuses it; keying on the number alone would then serve an entry written during the rolled-back request to a
later, unrelated deletion -- a deleted ancestor reading as fresh, which is the exact ``V2-D24`` failure this module
exists to prevent. A ``deletion_request.id`` is a ``uuid4`` and is never reused, so a recycled epoch cannot produce a
recycled token.

The contract this relies on: *every path that removes or rewrites an ancestor must go through ``delete_memory`` (which
moves the token)*. ``purge_working`` removes rows without moving it and is safe only because it never removes a row
that is cited as a source (finding ``E-038-F2``). A row replaced under a *new* request id also moves the token, so a
rewrite this module does not know about is caught by the same mechanism rather than trusted. The cache is
process-local, bounded (least recently used out) and eviction only forces a recomputation, never a different answer.

Only the hash and the ancestor ids are cached. The other causes move without a deletion (a retirement, a new method
version, the clock) so they are re-read on every call, each in one non-recursive statement over the cached ids.

Audit views are out of scope: this derives staleness **now**, from the current graph and clock. A past belief state
would need the graph as it was, so an ``audit_as_of`` renderer must not call it for a past time (``E-038-F3``)."""

from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256

from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import tuple_row

from custom_memory_engine.engine.channels import ArtifactView, Staleness, StalenessInputs, stale_of
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.soundness.admission import (
    DEFAULT_TAU_CONFIDENCE,
    DEFAULT_TAU_COVERAGE,
    MIN_REFUTATION_ATTEMPTS,
    Ledger,
    assess,
)

CLOSURE_HASH_VERSION = "closure-v1"
DEFAULT_CACHE_ENTRIES = 4096

#: The admission clauses that depend on the evidence ledger (support), as opposed to the refutation or negative-control
#: clauses, which are not a matter of support dropping (``STATISTICS.md`` 4.2).
SUPPORT_CLAUSES = frozenset(
    {"confidence_lower_below_threshold", "coverage_below_threshold", "zero_counter_opportunity_too_small"}
)

type CacheKey = tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class Closure:
    """An artifact's transitive source set and its digest."""

    digest: str
    sources: tuple[str, ...]


def digest_of(sources: Iterable[tuple[str, str]]) -> str:
    """The closure digest of ``(source_id, content)`` pairs, independent of the order they arrive in."""
    lines = sorted(f"{source_id}\t{canonical_hash(content)}" for source_id, content in sources)
    return sha256("\n".join((CLOSURE_HASH_VERSION, *lines)).encode("utf-8")).hexdigest()


async def compute_closure(
    connection: AsyncConnection, user_id: str, artifact_id: str, *, schema_name: str = "cme_engine"
) -> Closure:
    """Walk the derivation graph once (one recursive statement; ``UNION`` makes a cycle terminate) and hash it."""
    derivation = sql.Identifier(schema_name, "memory_derivation")
    versions = sql.Identifier(schema_name, "memory_version")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "WITH RECURSIVE ancestors(id) AS ("
                " SELECT source_version_id FROM {d} WHERE user_id = %(u)s AND derived_version_id = %(a)s"
                " UNION"
                " SELECT d.source_version_id FROM {d} d JOIN ancestors x ON d.derived_version_id = x.id"
                "  WHERE d.user_id = %(u)s"
                ") SELECT v.id, v.content FROM ancestors x JOIN {v} v ON v.id = x.id"
                " AND v.user_id = %(u)s ORDER BY v.id"
            ).format(d=derivation, v=versions),
            {"u": user_id, "a": artifact_id},
        )
        rows = [(str(row[0]), str(row[1])) for row in await cursor.fetchall()]
    return Closure(digest_of(rows), tuple(source_id for source_id, _ in rows))


@dataclass(slots=True)
class ClosureCache:
    """A bounded, process-local cache of closures. ``max_entries=0`` disables caching (every read recomputes).

    ``computations`` counts the hash walks this cache has performed, so a test can prove repeated reads of an unchanged
    artifact compute once (``W9``). :meth:`clear` empties it honestly.
    """

    max_entries: int = DEFAULT_CACHE_ENTRIES
    computations: int = 0
    _entries: "OrderedDict[CacheKey, Closure]" = field(default_factory=OrderedDict, init=False, repr=False)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def keys(self) -> list[CacheKey]:
        """The live cache keys, least recently used first (for tests and diagnostics)."""
        return list(self._entries)

    async def closure_of(
        self, connection: AsyncConnection, user_id: str, artifact_id: str, *, schema_name: str = "cme_engine"
    ) -> Closure:
        """The artifact's closure, cached while the user's deletion token has not moved since it was computed."""
        await _require_context(connection, user_id)
        token = await _deletion_token(connection, user_id, schema_name=schema_name)
        key: CacheKey = (schema_name, user_id, artifact_id, token)
        hit = self._entries.get(key)
        if hit is not None:
            self._entries.move_to_end(key)
            return hit
        closure = await compute_closure(connection, user_id, artifact_id, schema_name=schema_name)
        self.computations += 1
        if self.max_entries > 0:
            self._entries[key] = closure
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return closure


async def _deletion_token(connection: AsyncConnection, user_id: str, *, schema_name: str = "cme_engine") -> str:
    """``epoch:request_id`` of the user's latest deletion, or ``0:`` when there is none.

    The epoch alone is not a safe cache key: it is ``MAX(deletion_epoch) + 1``, so a rolled-back request returns its
    number to the pool and a later deletion reuses it. The request id is a ``uuid4`` and is never reused, so the pair
    identifies one committed deletion rather than a number that can be handed out twice.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT COALESCE(MAX(deletion_epoch), 0), "
                " (SELECT id FROM {r} WHERE user_id = %s ORDER BY deletion_epoch DESC, requested_at DESC, id DESC "
                "  LIMIT 1) "
                "FROM {r} WHERE user_id = %s"
            ).format(r=sql.Identifier(schema_name, "deletion_request")),
            (user_id, user_id),
        )
        row = await cursor.fetchone()
    epoch, request_id = (0, None) if row is None else (row[0], row[1])
    return f"{epoch}:{request_id or ''}"


async def _require_context(connection: AsyncConnection, user_id: str) -> None:
    """The caller owns the transaction and the tenant context; a mismatch is refused, never silently widened (I7)."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise InsufficientPrivilege("staleness_user_context_mismatch")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
    if row is None or row[0] != user_id:
        raise InsufficientPrivilege("staleness_user_context_mismatch")


def support_below_threshold(
    support: int,
    counter: int,
    undetermined: int,
    *,
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
) -> bool:
    """Whether the stored evidence ledger no longer passes the ``STATISTICS.md`` 4.2 support clauses.

    An empty ledger (``o = 0``) supports nothing. The thresholds are the documented admission defaults; ``V2-D30``
    leaves their final values open (finding ``E-038-F1``), so they are parameters rather than a silent choice.
    """
    if support + counter + undetermined == 0:
        return True
    assessment = assess(
        Ledger(support, counter, undetermined),
        refutation_attempts=MIN_REFUTATION_ATTEMPTS,
        negative_control_clean=True,
        tau_confidence=tau_confidence,
        tau_coverage=tau_coverage,
    )
    return any(reason in SUPPORT_CLAUSES for reason in assessment.reasons)


async def staleness_inputs(
    connection: AsyncConnection,
    user_id: str,
    artifact_id: str,
    *,
    cache: ClosureCache,
    schema_name: str = "cme_engine",
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
) -> StalenessInputs:
    """The five causes for one artifact, derived from the graph, the ledger, the method registry and the clock.

    Only a ``verified`` abstraction can be stale (an unverified one is already a hypothesis); anything else is
    all-false.
    A verified artifact that carries no sealed ``closed_over_hash`` cannot prove it is fresh, so it fails closed.
    """
    await _require_context(connection, user_id)
    fresh = StalenessInputs(False, False, False, False, False)
    stats = sql.Identifier(schema_name, "abstraction_stats")
    versions = sql.Identifier(schema_name, "memory_version")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT s.verification_status, s.closed_over_hash, s.support_count, s.counter_count,"
                " s.undetermined_count,"
                " s.expires_at, {clock}(), v.method_id, s.confidence_method_id"
                " FROM {s} s JOIN {v} v ON v.id = s.version_id AND v.user_id = s.user_id"
                " WHERE s.user_id = %s AND s.version_id = %s"
            ).format(s=stats, v=versions, clock=sql.Identifier(schema_name, "clock_now")),
            (user_id, artifact_id),
        )
        row = await cursor.fetchone()
        if row is None or row[0] != "verified":
            return fresh
        sealed, support, counter, undetermined, expires_at, now, method_id, confidence_method_id = row[1:]
        closure = await cache.closure_of(connection, user_id, artifact_id, schema_name=schema_name)
        source_retired = False
        if closure.sources:
            await cursor.execute(
                sql.SQL(
                    "SELECT EXISTS (SELECT 1 FROM {v} WHERE user_id = %s AND id = ANY(%s) AND lifecycle = 'retired')"
                ).format(v=versions),
                (user_id, list(closure.sources)),
            )
            exists = await cursor.fetchone()
            source_retired = bool(exists and exists[0])
        methods = [m for m in (method_id, confidence_method_id) if m is not None]
        method_changed = False
        if methods:
            await cursor.execute(
                sql.SQL(
                    "SELECT EXISTS (SELECT 1 FROM {m} old JOIN {m} newer ON newer.name = old.name"
                    " AND newer.registered_at > old.registered_at WHERE old.id = ANY(%s))"
                ).format(m=sql.Identifier(schema_name, "method")),
                (methods,),
            )
            exists = await cursor.fetchone()
            method_changed = bool(exists and exists[0])
    return StalenessInputs(
        closure_changed=sealed is None or sealed != closure.digest,
        support_below_threshold=support_below_threshold(
            support, counter, undetermined, tau_confidence=tau_confidence, tau_coverage=tau_coverage
        ),
        source_retired=source_retired,
        method_changed=method_changed,
        expired=isinstance(expires_at, datetime) and isinstance(now, datetime) and expires_at <= now,
    )


async def derive_staleness(
    connection: AsyncConnection,
    user_id: str,
    views: Sequence[ArtifactView],
    *,
    cache: ClosureCache,
    schema_name: str = "cme_engine",
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
) -> Staleness:
    """The ``Staleness`` function ``assemble`` takes, with every answer derived for the retrieved ``views`` up front.

    A verified view that was not among ``views`` reads as stale: an unknown artifact never defaults to fresh.
    """
    answers: dict[str, bool] = {}
    for view in views:
        if view.verification != "verified":
            continue
        inputs = await staleness_inputs(
            connection,
            user_id,
            view.artifact_id,
            cache=cache,
            schema_name=schema_name,
            tau_confidence=tau_confidence,
            tau_coverage=tau_coverage,
        )
        answers[view.artifact_id] = stale_of(inputs)

    def staleness(view: ArtifactView) -> bool:
        return answers.get(view.artifact_id, view.verification == "verified")

    return staleness


__all__ = [
    "CLOSURE_HASH_VERSION",
    "DEFAULT_CACHE_ENTRIES",
    "SUPPORT_CLAUSES",
    "Closure",
    "ClosureCache",
    "compute_closure",
    "derive_staleness",
    "digest_of",
    "staleness_inputs",
    "support_below_threshold",
]
