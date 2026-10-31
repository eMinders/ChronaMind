"""Canonical scope paths and exact subtree matching (V2 §5, B-01)."""

from custom_memory_engine.scope.paths import (
    MAX_DEPTH,
    MAX_READ_SCOPE_PREFIXES,
    MAX_SEGMENT_LENGTH,
    ScopeError,
    ScopePath,
    is_descendant,
    normalize_read_prefixes,
    parse_scope,
    subtree_predicate,
    user_scope,
)

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
