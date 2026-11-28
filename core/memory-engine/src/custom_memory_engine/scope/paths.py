"""Canonical scope paths and exact-subtree matching (V2 §5 rule 4, B-01).

Why this module exists
----------------------
`ARCHITECTURE_V2.md` §5 rule 4 originally specified scope filtering as
``path_with_prefix LIKE ANY(...)``. In SQL, ``LIKE`` treats ``_`` as "any single
character" and ``%`` as "any sequence", so a selector built from ``/u/a_b/`` also
matches another tenant's ``/u/aXb/``. A trailing separator does not help: the
wildcard is *inside* the identifier. Two tenants whose ids differ only by a
character in the wildcard position therefore read each other's rows
(reproduced on PostgreSQL 16; see ``PLAN/V2/audits/OPEN_DECISIONS_AND_BLOCKERS.md``
B-01).

The fix has three independent parts, and all three are required:

1. **Literal comparison.** Subtree membership is decided by ``starts_with``
   (``pg_catalog.starts_with`` / ``^@``), never by ``LIKE``. Wildcard characters
   in an identifier are then ordinary characters with no special meaning.
2. **Canonical form.** A path is a sequence of non-empty segments, each
   validated to exclude ``/``, NUL and control characters, rendered with a
   leading *and* trailing separator. Because no segment may contain ``/``,
   string-prefix and segment-prefix coincide exactly: ``/u/a/`` is a prefix of
   ``/u/a/b/`` and is *not* a prefix of ``/u/ab/``.
3. **Boundary validation.** Segments are validated before any statement runs, so
   a malformed path fails closed rather than reaching SQL.

Exactness is a property of the representation, not of a caller's care: given a
canonical path, ``is_descendant`` is exact for arbitrary identifiers, including
adversarial ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from psycopg import sql

SCOPE_SEPARATOR = "/"
MAX_SEGMENT_LENGTH = 128
MAX_DEPTH = 16
MIN_READ_SCOPE_PREFIXES = 1
MAX_READ_SCOPE_PREFIXES = 16

# Control characters (C0, DEL, C1) may never appear in a path segment: they are
# invisible in logs, and they break the invariant that a rendered path is a
# faithful, comparable encoding of its segments.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Scope segments are engine-generated and deliberately narrow. Excluding the SQL
# wildcard characters here is defence in depth: part 1 already makes them
# literal, but a narrow charset means a future mistaken `LIKE` cannot silently
# reintroduce the B-01 leak.
_SCOPE_SEGMENT = re.compile(r"[a-z0-9][a-z0-9-]*\Z")

# User ids are external. They are kept broad for compatibility with v1's
# `app_user.id` values, but may not contain a separator, control characters, or
# a NUL, and may not be blank.
_USER_SEGMENT_MAX = 256


class ScopeError(ValueError):
    """A path failed validation. The message is a static code, never the input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True, order=True)
class ScopePath:
    """A canonical, validated scope path such as ``/u/alice/projects/apollo/``.

    Invariants:

    * ``segments`` is non-empty and every segment is non-empty;
    * no segment contains ``/``, NUL, or a control character;
    * ``text`` is exactly ``/`` + ``/``.join(segments) + ``/``.

    A ``ScopePath`` cannot be constructed in a non-canonical state, so
    ``str(path)`` is a faithful key for equality, ordering and prefix tests.
    """

    segments: tuple[str, ...]
    text: str

    @property
    def depth(self) -> int:
        """Return the number of segments (``/u/alice/`` has depth 2)."""
        return len(self.segments)

    @property
    def parent(self) -> ScopePath | None:
        """Return the enclosing path, or ``None`` for a root path."""
        if len(self.segments) <= 1:
            return None
        return ScopePath(
            self.segments[:-1], SCOPE_SEPARATOR + SCOPE_SEPARATOR.join(self.segments[:-1]) + SCOPE_SEPARATOR
        )

    def child(self, segment: str) -> ScopePath:
        """Return the strict descendant obtained by appending one scope segment."""
        _require_scope_segment(segment)
        combined = (*self.segments, segment)
        if len(combined) > MAX_DEPTH:
            raise ScopeError("scope_depth_exceeded")
        return ScopePath(combined, SCOPE_SEPARATOR + SCOPE_SEPARATOR.join(combined) + SCOPE_SEPARATOR)

    def is_prefix_of(self, other: ScopePath) -> bool:
        """Return whether ``self`` is an ancestor of, or equal to, ``other``.

        Exact by construction: both operands are canonical, so a string prefix
        is a segment prefix. ``/u/a/`` is a prefix of ``/u/a/b/``; ``/u/a/`` is
        not a prefix of ``/u/ab/``.
        """
        return other.text.startswith(self.text)

    def is_strict_ancestor_of(self, other: ScopePath) -> bool:
        """Return whether ``self`` encloses ``other`` without being equal to it."""
        return self != other and self.is_prefix_of(other)

    def is_within(self, ancestor: ScopePath) -> bool:
        """Return whether ``self`` is ``ancestor`` or one of its descendants."""
        return ancestor.is_prefix_of(self)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"ScopePath({self.text!r})"


def _require_scope_segment(segment: object) -> str:
    if not isinstance(segment, str):
        raise ScopeError("scope_segment_must_be_a_string")
    if not segment or len(segment) > MAX_SEGMENT_LENGTH:
        raise ScopeError("scope_segment_length_invalid")
    if _CONTROL.search(segment) is not None:
        raise ScopeError("scope_segment_must_not_contain_control_characters")
    if SCOPE_SEPARATOR in segment:
        raise ScopeError("scope_segment_must_not_contain_separator")
    if _SCOPE_SEGMENT.fullmatch(segment) is None:
        # Rejects `%`, `_`, `.`, whitespace and uppercase in engine-generated scopes.
        raise ScopeError("scope_segment_charset_invalid")
    return segment


def _require_user_segment(segment: object) -> str:
    if not isinstance(segment, str):
        raise ScopeError("user_id_must_be_a_string")
    if not segment or not segment.strip():
        raise ScopeError("nonblank_user_id_required")
    if segment != segment.strip():
        raise ScopeError("user_id_must_not_have_surrounding_whitespace")
    if len(segment) > _USER_SEGMENT_MAX:
        raise ScopeError("user_id_too_long")
    if segment == "." or segment == "..":
        raise ScopeError("user_id_may_not_be_a_relative_reference")
    if _CONTROL.search(segment) is not None:
        raise ScopeError("user_id_must_not_contain_control_characters")
    if SCOPE_SEPARATOR in segment:
        raise ScopeError("user_id_must_not_contain_separator")
    return segment


def parse_scope(text: object) -> ScopePath:
    """Parse a rendered path into a canonical :class:`ScopePath`.

    Accepts the canonical form only (leading and trailing separator). A path
    missing its trailing separator is rejected rather than silently normalized,
    because normalizing on input is how a caller's typo becomes someone else's
    authorization decision.
    """
    if not isinstance(text, str):
        raise ScopeError("scope_must_be_a_string")
    if not text:
        raise ScopeError("scope_must_not_be_blank")
    if _CONTROL.search(text) is not None:
        raise ScopeError("scope_must_not_contain_control_characters")
    if not text.startswith(SCOPE_SEPARATOR):
        raise ScopeError("scope_must_start_with_separator")
    if len(text) == 1:
        raise ScopeError("scope_must_have_at_least_one_segment")
    if not text.endswith(SCOPE_SEPARATOR):
        raise ScopeError("scope_must_end_with_separator")
    raw = text[1:-1].split(SCOPE_SEPARATOR)
    if any(part == "" for part in raw):
        raise ScopeError("scope_must_not_contain_empty_segment")
    if len(raw) > MAX_DEPTH:
        raise ScopeError("scope_depth_exceeded")
    if raw[0] != "u":
        raise ScopeError("scope_must_begin_with_user_namespace")
    if len(raw) < 2:
        raise ScopeError("scope_must_include_user_id")
    segments = ["u", _require_user_segment(raw[1])]
    for part in raw[2:]:
        segments.append(_require_scope_segment(part))
    return ScopePath(tuple(segments), text)


def user_scope(user_id: object) -> ScopePath:
    """Return the conversational profile root ``/u/<user_id>/`` for ``user_id``.

    This is the v1-compatible root: `ARCHITECTURE_V2.md` §5 states that v1's
    ``user_id`` semantics are exactly recoverable as this path.
    """
    return parse_scope(SCOPE_SEPARATOR + "u" + SCOPE_SEPARATOR + _require_user_segment(user_id) + SCOPE_SEPARATOR)


def normalize_read_prefixes(prefixes: object) -> tuple[str, ...]:
    """Validate a selector set and return canonical path strings, de-duplicated.

    Order is preserved for deterministic SQL and logging. The cap mirrors the
    §5.1 declared limit and is a resource error, never a silent truncation: a
    dropped selector would narrow the read set invisibly.
    """
    if prefixes is None:
        raise ScopeError("read_prefixes_required")
    if isinstance(prefixes, str):
        raise ScopeError("read_prefixes_must_be_a_sequence")
    try:
        items = tuple(prefixes)  # type: ignore[arg-type]
    except TypeError as error:
        raise ScopeError("read_prefixes_must_be_a_sequence") from error
    if not items:
        raise ScopeError("at_least_one_read_prefix_required")
    if len(items) > MAX_READ_SCOPE_PREFIXES:
        raise ScopeError("read_prefix_limit_exceeded")
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(parse_scope(item).text, None)
    return tuple(seen)


def is_descendant(path: object, ancestor: object) -> bool:
    """Return whether ``path`` equals ``ancestor`` or lies inside it."""
    return parse_scope(path).is_within(parse_scope(ancestor))


_COLUMN_REFERENCE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")


def subtree_predicate(column: str, prefixes: object) -> tuple[sql.Composed, tuple[str, ...]]:
    """Build an exact subtree SQL predicate and its bound parameters.

    Returns ``(predicate, params)``. ``predicate`` is a parenthesised
    ``psycopg.sql.Composed`` boolean expression over ``column``; ``params`` are
    the validated canonical prefixes, one per ``%s`` placeholder.

    The predicate uses ``pg_catalog.starts_with`` and never ``LIKE``, so ``%``
    and ``_`` inside an identifier are literal (B-01). ``column`` is quoted as an
    identifier (``name`` or ``alias.name``), never interpolated as text, so it
    cannot carry SQL.
    """
    validated = normalize_read_prefixes(prefixes)
    if not isinstance(column, str) or _COLUMN_REFERENCE.fullmatch(column) is None:
        raise ScopeError("untrusted_column_reference")
    identifier = sql.Identifier(*column.split("."))
    clause = sql.SQL("pg_catalog.starts_with({}, %s)").format(identifier)
    return sql.SQL("({})").format(sql.SQL(" OR ").join([clause] * len(validated))), validated


__all__ = [
    "MAX_DEPTH",
    "MAX_READ_SCOPE_PREFIXES",
    "MAX_SEGMENT_LENGTH",
    "ScopeError",
    "ScopePath",
    "is_descendant",
    "normalize_read_prefixes",
    "parse_scope",
    "subtree_predicate",
    "user_scope",
]
