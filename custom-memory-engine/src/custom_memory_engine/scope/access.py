"""Read-set authorization over scope grants (V2 §5 rules 2–3; V2-D36, resolves B-02).

The effective read set is the union of the caller's selectors, intersected with the
caller's grants (§5 rule 2). The rules, each fail-closed:

1. **Own namespace.** Selectors inside ``/u/<caller>/`` are always authorized, for
   every requested visibility.
2. **Another namespace requires an active grant** whose subtree contains the
   selector path. Revoked grants count as absent. A selector *broader* than every
   grant is denied, never silently narrowed: "nothing is implicit".
3. **`private` is never readable across users.** A cross-user selector keeps only
   ``project``/``shared``; if nothing remains it is denied (§5 rule 3).
4. **Row visibility is evaluated against the reading selector, not the row's home**
   (§5 rule 3): a ``private`` row matches only a selector whose path *equals* the
   row's scope path; ``project``/``shared`` rows match any selector whose subtree
   contains them.

``row_visible`` is the reference semantics; ``read_predicate`` is the SQL that
``E-011`` uses on rows carrying ``scope_path``/``visibility`` (migration M2). The
two are tested against each other on PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.scope.paths import (
    MAX_READ_SCOPE_PREFIXES,
    ScopeError,
    ScopePath,
    parse_scope,
    user_scope,
)

type Visibility = Literal["private", "project", "shared"]
VISIBILITIES: tuple[Visibility, ...] = get_args(Visibility.__value__)
CROSS_USER_VISIBILITIES: frozenset[Visibility] = frozenset({"project", "shared"})

type DenialReason = Literal["no_active_grant", "private_not_readable_across_users"]


@dataclass(frozen=True, slots=True)
class Selector:
    """One requested read: a scope subtree and the visibilities wanted from it."""

    path: ScopePath
    visibilities: frozenset[Visibility]

    @classmethod
    def of(cls, path: object, visibilities: Iterable[object] = ("private", "project", "shared")) -> Selector:
        wanted = frozenset(visibilities)
        if not wanted:
            raise ScopeError("selector_visibility_required")
        unknown = wanted - set(VISIBILITIES)
        if unknown:
            raise ScopeError("selector_visibility_invalid")
        return cls(parse_scope(path), frozenset(wanted))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ReadEntry:
    """An authorized selector after intersection with the caller's grants."""

    path: str
    visibilities: frozenset[Visibility]


@dataclass(frozen=True, slots=True)
class Denial:
    path: str
    reason: DenialReason


@dataclass(frozen=True, slots=True)
class ReadSet:
    entries: tuple[ReadEntry, ...]
    denied: tuple[Denial, ...]

    @property
    def empty(self) -> bool:
        """An empty read set reads nothing (it is never a wildcard)."""
        return not self.entries


def resolve_read_set(
    caller_user_id: object, selectors: Sequence[Selector], active_grants: Iterable[object]
) -> ReadSet:
    """Intersect ``selectors`` with the caller's namespace and active grants."""
    own = user_scope(caller_user_id)
    if not selectors:
        raise ScopeError("at_least_one_read_prefix_required")
    if len(selectors) > MAX_READ_SCOPE_PREFIXES:
        raise ScopeError("read_prefix_limit_exceeded")
    grants = tuple(parse_scope(path) for path in active_grants)
    entries: dict[str, set[Visibility]] = {}
    denied: list[Denial] = []
    for selector in selectors:
        if not isinstance(selector, Selector):
            raise ScopeError("selector_required")
        if selector.path.is_within(own):
            entries.setdefault(selector.path.text, set()).update(selector.visibilities)
            continue
        if not any(selector.path.is_within(grant) for grant in grants):
            denied.append(Denial(selector.path.text, "no_active_grant"))
            continue
        allowed = selector.visibilities & CROSS_USER_VISIBILITIES
        if not allowed:
            denied.append(Denial(selector.path.text, "private_not_readable_across_users"))
            continue
        entries.setdefault(selector.path.text, set()).update(allowed)
    return ReadSet(tuple(ReadEntry(path, frozenset(v)) for path, v in entries.items()), tuple(denied))


def row_visible(read_set: ReadSet, row_scope_path: object, row_visibility: object) -> bool:
    """Reference semantics: may a row at ``row_scope_path`` with ``row_visibility`` be read?"""
    if row_visibility not in VISIBILITIES:
        return False  # unknown visibility fails closed
    try:
        row_path = parse_scope(row_scope_path)
    except ScopeError:
        return False
    for entry in read_set.entries:
        if row_visibility not in entry.visibilities:
            continue
        if row_visibility == "private":
            if row_path.text == entry.path:
                return True
        elif row_path.is_within(parse_scope(entry.path)):
            return True
    return False


def read_predicate(read_set: ReadSet, path_column: str, visibility_column: str) -> tuple[sql.Composed, list[object]]:
    """SQL equivalent of :func:`row_visible`. An empty read set yields ``false``."""
    for column in (path_column, visibility_column):
        if not all(part.isidentifier() for part in column.split(".")) or column.count(".") > 1:
            raise ScopeError("untrusted_column_reference")
    path = sql.Identifier(*path_column.split("."))
    visibility = sql.Identifier(*visibility_column.split("."))
    clauses: list[sql.Composable] = []
    params: list[object] = []
    for entry in read_set.entries:
        if "private" in entry.visibilities:
            clauses.append(sql.SQL("({} = %s AND {} = 'private')").format(path, visibility))
            params.append(entry.path)
        shared = sorted(v for v in entry.visibilities if v != "private")
        if shared:
            clauses.append(
                sql.SQL("(pg_catalog.starts_with({}, %s) AND {} = ANY(%s::text[]))").format(path, visibility)
            )
            params.extend([entry.path, shared])
    if not clauses:
        return sql.Composed([sql.SQL("false")]), []
    return sql.SQL("({})").format(sql.SQL(" OR ").join(clauses)), params


async def active_grant_paths(
    connection: AsyncConnection, caller_user_id: str, *, schema_name: str = "cme_engine"
) -> tuple[str, ...]:
    """Return the caller's unrevoked grant paths (RLS restricts rows to owner/grantee)."""
    user_scope(caller_user_id)  # validates the identity before any statement
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT scope_path FROM {g} WHERE principal_id = %s AND revoked_at IS NULL ORDER BY scope_path"
            ).format(g=sql.Identifier(schema_name, "scope_grant")),
            (caller_user_id,),
        )
        return tuple(str(row[0]) for row in await cursor.fetchall())


async def resolve(
    connection: AsyncConnection,
    caller_user_id: str,
    selectors: Sequence[Selector],
    *,
    schema_name: str = "cme_engine",
) -> ReadSet:
    """Resolve a read set from live grants."""
    grants = await active_grant_paths(connection, caller_user_id, schema_name=schema_name)
    return resolve_read_set(caller_user_id, selectors, grants)


__all__ = [
    "CROSS_USER_VISIBILITIES",
    "VISIBILITIES",
    "Denial",
    "ReadEntry",
    "ReadSet",
    "Selector",
    "Visibility",
    "active_grant_paths",
    "read_predicate",
    "resolve",
    "resolve_read_set",
    "row_visible",
]
