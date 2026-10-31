"""MA-011 isolation cases, shared by the pure suite and the PostgreSQL suite (and by ``E-011`` later).

Each case is a caller, a selector set, and the exact set of rows that must be returned from a fixed
universe. The universe deliberately contains identifier-prefix siblings (``/u/4/`` vs ``/u/42/``,
``/u/4x/``, ``/u/40/``) and wildcard-shaped identifiers (``/u/a_b/`` vs ``/u/aXb/``).

``MATCHERS`` are the candidate *subtree-matching predicates*, as pure functions with their SQL twins.
Two reproduce historical designs and must fail; ``starts_with`` is the decided one (``V2-D35``):

* ``like_without_separator``: the r2 text, ``path LIKE S || '%'`` with ``S`` lacking the trailing ``/``;
* ``like_with_separator``: the approved ``LIKE`` text with the trailing separator (still leaks via ``_``);
* ``starts_with``: ``pg_catalog.starts_with(path, S)`` over canonical paths.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from custom_memory_engine.scope.access import Selector

type Row = tuple[str, str]

USERS = ("4", "42", "4x", "40", "a_b", "aXb")
ROWS: tuple[Row, ...] = (
    ("/u/4/", "private"),
    ("/u/4/", "project"),
    ("/u/4/p/a/", "private"),
    ("/u/4/p/a/", "project"),
    ("/u/4/p/b/", "project"),
    ("/u/42/", "private"),
    ("/u/42/", "shared"),
    ("/u/4x/", "project"),
    ("/u/40/p/a/", "shared"),
    ("/u/a_b/", "private"),
    ("/u/a_b/p/r/", "project"),
    ("/u/aXb/", "private"),
    ("/u/aXb/p/r/", "project"),
)


@dataclass(frozen=True)
class Case:
    number: int
    name: str
    caller: str
    selectors: tuple[Selector, ...]
    expected: frozenset[Row]
    grants: tuple[str, ...] = ()


CASES: tuple[Case, ...] = (
    Case(
        1,
        "prefix: subtree('/u/4/') excludes /u/42/, /u/4x/, /u/40/",
        "4",
        (Selector.subtree("/u/4/"),),
        frozenset({("/u/4/", "private"), ("/u/4/", "project"), ("/u/4/p/a/", "project"), ("/u/4/p/b/", "project")}),
    ),
    Case(
        2,
        "exact('/u/4/') excludes /u/4/p/a/",
        "4",
        (Selector.exact("/u/4/"),),
        frozenset({("/u/4/", "private"), ("/u/4/", "project")}),
    ),
    Case(
        3,
        "no implicit ancestors: subtree('/u/4/p/a/') excludes /u/4/",
        "4",
        (Selector.subtree("/u/4/p/a/"),),
        frozenset({("/u/4/p/a/", "private"), ("/u/4/p/a/", "project")}),
    ),
    Case(
        4,
        "visibility: project row in /u/4/p/a/ is not readable from sibling /u/4/p/b/",
        "4",
        (Selector.subtree("/u/4/p/b/"),),
        frozenset({("/u/4/p/b/", "project")}),
    ),
    Case(
        5,
        "metacharacters: subtree('/u/a_b/') excludes /u/aXb/",
        "a_b",
        (Selector.subtree("/u/a_b/"),),
        frozenset({("/u/a_b/", "private"), ("/u/a_b/p/r/", "project")}),
    ),
    Case(
        6,
        "cross-user grant: never private, only inside the grant",
        "4",
        (Selector.with_visibility("/u/42/", ("project", "shared")),),
        frozenset({("/u/42/", "shared")}),
        grants=("/u/42/",),
    ),
)


def _like(pattern: str, text: str) -> bool:
    """SQL ``LIKE`` with the default escape: ``_`` is one character, ``%`` any run."""
    regex = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, text, flags=re.DOTALL) is not None


MATCHERS: dict[str, tuple[Callable[[str, str], bool], str]] = {
    "like_without_separator": (
        lambda row, prefix: _like(prefix.rstrip("/") + "%", row), "{p} LIKE rtrim(%s, '/') || '%%'"
    ),
    "like_with_separator": (lambda row, prefix: _like(prefix + "%", row), "{p} LIKE %s || '%%'"),
    "starts_with": (lambda row, prefix: row.startswith(prefix), "pg_catalog.starts_with({p}, %s)"),
}  # fmt: skip

#: Which cases each flawed matcher must fail. The decided matcher must fail none.
EXPECTED_FAILURES: dict[str, frozenset[int]] = {
    "like_without_separator": frozenset({1, 5}),
    "like_with_separator": frozenset({5}),
    "starts_with": frozenset(),
}


def evaluate(case: Case, matcher: Callable[[str, str], bool]) -> frozenset[Row]:
    """The read semantics of ``row_visible`` with the subtree test replaced by ``matcher``.

    No ACL filter is applied: the cases' selectors are all authorized for their caller (the pure suite
    checks that), so the only thing that varies is the matching predicate.
    """
    result: set[Row] = set()
    for path, visibility in ROWS:
        for selector in case.selectors:
            if visibility not in selector.visibilities:
                continue
            prefix = selector.path.text
            if selector.mode == "exact" or visibility == "private":
                hit = path == prefix
            else:
                hit = matcher(path, prefix)
            if hit:
                result.add((path, visibility))
    return frozenset(result)
