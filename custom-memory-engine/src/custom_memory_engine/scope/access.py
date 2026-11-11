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

5. **Exact selectors** (``exact(path)``, API §6) match rows whose scope equals the path,
   at every requested visibility; a subtree selector also matches descendants.
6. **Writes** (§5 rule 1, ``V2-D53``): a write into ``(scope, visibility)`` is authorized only
   inside the caller's own namespace ``/u/<caller>/``. A grant confers **read** membership
   only (``V2-D36``): a grantee can neither write nor delete in the owner's namespace.
   Anything else is ``forbidden``, before any row is constructed (:func:`authorize_write`).

Two independent layers (§5 rule 4, ``V2-D45``):

* the **application layer** computes the read set from the request's selectors
  (:func:`resolve_read_set`); ``row_visible`` is its reference semantics and
  ``read_predicate`` its SQL twin, which ``E-011`` uses in queries;
* the **RLS layer** enforces the caller's *ACL* only, computed inside PostgreSQL from
  ``app.user_id`` and ``scope_grant`` (:func:`rls_read_using`; reference
  :func:`acl_readable`). It does not trust any value the application computed, so a bug
  or bypass in the application layer cannot widen what RLS returns. Every application
  read set is contained in the ACL (tested exhaustively).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
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
type SelectorMode = Literal["subtree", "exact"]


@dataclass(frozen=True, slots=True)
class Selector:
    """One requested read: a scope subtree and the visibilities wanted from it."""

    path: ScopePath
    visibilities: frozenset[Visibility]
    mode: SelectorMode = "subtree"

    @classmethod
    def of(
        cls,
        path: object,
        visibilities: Iterable[object] = VISIBILITIES,
        mode: object = "subtree",
    ) -> Selector:
        wanted = frozenset(visibilities)
        if not wanted:
            raise ScopeError("selector_visibility_required")
        unknown = wanted - set(VISIBILITIES)
        if unknown:
            raise ScopeError("selector_visibility_invalid")
        if mode not in ("subtree", "exact"):
            raise ScopeError("selector_mode_invalid")
        return cls(parse_scope(path), frozenset(wanted), mode)  # type: ignore[arg-type]

    @classmethod
    def exact(cls, path: object) -> Selector:
        """``exact(path)``: rows whose scope equals ``path``."""
        return cls.of(path, VISIBILITIES, "exact")

    @classmethod
    def subtree(cls, path: object) -> Selector:
        """``subtree(path)``: rows at ``path`` or below it."""
        return cls.of(path, VISIBILITIES, "subtree")

    @classmethod
    def with_visibility(cls, path: object, visibilities: Iterable[object]) -> Selector:
        """``with_visibility(path, {project, shared})``: a subtree restricted to the named visibilities."""
        return cls.of(path, visibilities, "subtree")


@dataclass(frozen=True, slots=True)
class ReadEntry:
    """An authorized selector after intersection with the caller's grants."""

    path: str
    visibilities: frozenset[Visibility]
    mode: SelectorMode = "subtree"


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
    entries: dict[tuple[str, SelectorMode], set[Visibility]] = {}
    denied: list[Denial] = []
    for selector in selectors:
        if not isinstance(selector, Selector):
            raise ScopeError("selector_required")
        if selector.path.is_within(own):
            entries.setdefault((selector.path.text, selector.mode), set()).update(selector.visibilities)
            continue
        if not any(selector.path.is_within(grant) for grant in grants):
            denied.append(Denial(selector.path.text, "no_active_grant"))
            continue
        allowed = selector.visibilities & CROSS_USER_VISIBILITIES
        if not allowed:
            denied.append(Denial(selector.path.text, "private_not_readable_across_users"))
            continue
        entries.setdefault((selector.path.text, selector.mode), set()).update(allowed)
    return ReadSet(tuple(ReadEntry(path, frozenset(v), mode) for (path, mode), v in entries.items()), tuple(denied))


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
        if entry.mode == "exact" or row_visibility == "private":
            if row_path.text == entry.path:
                return True
        elif row_path.is_within(parse_scope(entry.path)):
            return True
    return False


def _read_clauses(
    read_set: ReadSet,
    path_column: str,
    visibility_column: str,
    placeholder: Callable[[str], sql.Composable],
    name: Callable[[int], str],
) -> tuple[list[sql.Composable], dict[str, object]]:
    """The selector clauses of :func:`row_visible`, with pluggable parameter placeholders.

    One implementation serves both the positional form (:func:`read_predicate`) and the named form
    (:func:`read_predicate_named`). The loop is not duplicated because this *is* the security predicate:
    two copies would drift, and a drift here is a disclosure bug (V2 §5 rule 4, threat T-06).
    """
    for column in (path_column, visibility_column):
        if not all(part.isidentifier() for part in column.split(".")) or column.count(".") > 1:
            raise ScopeError("untrusted_column_reference")
    path = sql.Identifier(*path_column.split("."))
    visibility = sql.Identifier(*visibility_column.split("."))
    clauses: list[sql.Composable] = []
    values: dict[str, object] = {}
    pending: list[object] = []

    def bind(value: object) -> sql.Composable:
        pending.append(value)
        return placeholder(name(len(pending) - 1))

    for entry in read_set.entries:
        if entry.mode == "exact":
            clauses.append(
                sql.SQL("({} = {} AND {} = ANY({}::text[]))").format(
                    path, bind(entry.path), visibility, bind(sorted(entry.visibilities))
                )
            )
            continue
        if "private" in entry.visibilities:
            clauses.append(sql.SQL("({} = {} AND {} = 'private')").format(path, bind(entry.path), visibility))
        shared = sorted(v for v in entry.visibilities if v != "private")
        if shared:
            clauses.append(
                sql.SQL("(pg_catalog.starts_with({}, {}) AND {} = ANY({}::text[]))").format(
                    path, bind(entry.path), visibility, bind(shared)
                )
            )
    for index, value in enumerate(pending):
        values[name(index)] = value
    return clauses, values


def read_predicate(read_set: ReadSet, path_column: str, visibility_column: str) -> tuple[sql.Composed, list[object]]:
    """SQL equivalent of :func:`row_visible`, bound positionally (``%s``). An empty read set yields ``false``."""
    clauses, values = _read_clauses(read_set, path_column, visibility_column, lambda _key: sql.SQL("%s"), str)
    if not clauses:
        return sql.Composed([sql.SQL("false")]), []
    # values are keyed in bind order, which is the order the %s placeholders appear in the clauses
    return sql.SQL("({})").format(sql.SQL(" OR ").join(clauses)), list(values.values())


def _placeholder(key: str) -> sql.SQL:
    """The named placeholder ``%(read_<digits>)s`` for ``key``, refusing any other shape.

    It is one literal rather than ``sql.SQL("%({})s").format(...)``: ``format`` would compose the name as a
    separate part, and ``sql.Identifier`` would *quote* it (psycopg would then look for a parameter named
    ``"read_0"``). ``key`` is a counter this module generates, so the digits alone decide it.
    """
    if not key.startswith("read_") or not key.removeprefix("read_").isdigit():
        raise ScopeError("untrusted_parameter_name")
    return sql.SQL(f"%({key})s")  # type: ignore[arg-type]  # only the validated digits reach the format


def read_predicate_named(
    read_set: ReadSet, path_column: str, visibility_column: str
) -> tuple[sql.Composed, dict[str, object]]:
    """The same predicate with named placeholders, so it can join a statement that already uses ``%(name)s``.

    ``compile_predicate`` (the temporal filter) binds by name. Psycopg cannot mix named and positional
    parameters in one statement, so a query that applies both must use this form.
    """
    clauses, values = _read_clauses(
        read_set,
        path_column,
        visibility_column,
        _placeholder,
        lambda index: f"read_{index}",
    )
    if not clauses:
        return sql.Composed([sql.SQL("false")]), {}
    return sql.SQL("({})").format(sql.SQL(" OR ").join(clauses)), values


def acl_readable(
    caller_user_id: object, active_grants: Iterable[object], row_scope_path: object, row_visibility: object
) -> bool:
    """Reference semantics of the RLS layer: the caller's ACL, independent of any selector.

    A row is readable if it lies in the caller's own namespace (any visibility), or if it is
    ``project``/``shared`` and lies inside an active grant. Anything malformed fails closed.
    """
    if row_visibility not in VISIBILITIES:
        return False
    try:
        own = user_scope(caller_user_id)
        row_path = parse_scope(row_scope_path)
        grants = tuple(parse_scope(path) for path in active_grants)
    except ScopeError:
        return False
    if row_path.is_within(own):
        return True
    return row_visibility in CROSS_USER_VISIBILITIES and any(row_path.is_within(g) for g in grants)


def authorize_write(
    caller_user_id: object, scope: object, visibility: object, active_grants: Iterable[object] = ()
) -> ScopePath:
    """§5 rule 1 (``V2-D53``): return the target scope if it lies in the caller's own namespace.

    ``active_grants`` is accepted and deliberately ignored: grants are read-only (``V2-D36``), so
    holding one never authorizes a write or a deletion. Raises ``ScopeError('forbidden')`` otherwise
    (the API maps it to ``forbidden``); it runs before any row is constructed and reveals nothing about
    why. The written row is always readable back by its writer, which keeps rule 1 satisfied.
    """
    del active_grants  # read membership only
    try:
        own = user_scope(caller_user_id)
        target = parse_scope(scope)
    except ScopeError:
        raise ScopeError("forbidden") from None
    if visibility not in VISIBILITIES or not target.is_within(own):
        raise ScopeError("forbidden")
    return target


_CALLER = sql.SQL("NULLIF(pg_catalog.current_setting('app.user_id', true), '')")


def rls_read_using(
    table: str,
    path_column: str = "scope_path",
    visibility_column: str = "visibility",
    grant_table: tuple[str, ...] = ("scope_grant",),
) -> sql.Composed:
    """The RLS ``USING`` expression that ``E-011`` attaches to every scoped table (``V2-D45``).

    Static SQL (a policy cannot take bind parameters). It reads the caller only from
    ``app.user_id`` (set by ``security.tenant_scope``) and grants only from ``scope_grant``,
    whose own RLS shows a grantee its grants. An unset identity, or one containing the
    separator, reads nothing.

    The row's columns are qualified with ``table`` (the policy's table name, unqualified).
    Unqualified, a ``scope_path`` column inside the grant sub-query would bind to
    ``scope_grant.scope_path`` and make every grant match every row (measured: the isolation
    suite fails that way), so qualification is mandatory, not stylistic.
    """
    for column in (table, path_column, visibility_column):
        if not column.isidentifier():
            raise ScopeError("untrusted_column_reference")
    if not 1 <= len(grant_table) <= 2 or not all(part.isidentifier() for part in grant_table):
        raise ScopeError("untrusted_column_reference")
    return sql.SQL(
        "(pg_catalog.strpos(COALESCE({caller}, '/'), '/') = 0 AND ("
        "pg_catalog.starts_with({path}, '/u/' || {caller} || '/') "
        "OR ({visibility} IN ('project', 'shared') AND EXISTS ("
        "SELECT 1 FROM {grants} AS g WHERE g.principal_id = {caller} AND g.revoked_at IS NULL "
        "AND pg_catalog.starts_with({path}, g.scope_path)))))"
    ).format(
        caller=_CALLER,
        path=sql.Identifier(table, path_column),
        visibility=sql.Identifier(table, visibility_column),
        grants=sql.Identifier(*grant_table),
    )


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
    "SelectorMode",
    "Visibility",
    "acl_readable",
    "active_grant_paths",
    "authorize_write",
    "read_predicate",
    "read_predicate_named",
    "resolve",
    "resolve_read_set",
    "rls_read_using",
    "row_visible",
]
