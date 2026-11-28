"""B-01 regression: scope matching must be exact for adversarial identifiers.

The defect these tests encode is real and was reproduced on PostgreSQL 16:
``'/u/a_b/' LIKE '/u/a_%'`` is true, so `_` matched the other tenant's `aXb`.
Every test below that names a wildcard character is a direct regression test for
that leak. The trailing-separator fix alone does not satisfy them.
"""

import pytest

from custom_memory_engine.scope.paths import (
    MAX_READ_SCOPE_PREFIXES,
    ScopeError,
    ScopePath,
    is_descendant,
    normalize_read_prefixes,
    parse_scope,
    subtree_predicate,
    user_scope,
)


def test_user_scope_is_v1_compatible_root() -> None:
    assert str(user_scope("alice")) == "/u/alice/"


def test_scope_is_canonical_and_round_trips() -> None:
    path = parse_scope("/u/alice/projects/apollo/")
    assert path.segments == ("u", "alice", "projects", "apollo")
    assert str(path) == "/u/alice/projects/apollo/"
    assert parse_scope(str(path)) == path


# --- the B-01 defect itself -------------------------------------------------


def test_underscore_in_transaction_id_does_not_leak_sibling_tenant() -> None:
    """`/u/a_b/` must not match `/u/aXb/` — the exact reproduced leak."""
    victim = parse_scope("/u/a_b/")
    attacker = parse_scope("/u/aXb/")

    assert not victim.is_prefix_of(attacker)
    assert not attacker.is_prefix_of(victim)
    assert not is_descendant("/u/aXb/", "/u/a_b/")


def test_percent_in_user_id_does_not_match_everything() -> None:
    """A `%` is literal; it must not behave as an any-sequence wildcard."""
    assert not parse_scope("/u/%/").is_prefix_of(parse_scope("/u/alice/"))
    assert is_descendant("/u/%/private/", "/u/%/")


def test_wildcard_shaped_prefix_does_not_widen_read_set() -> None:
    selector = normalize_read_prefixes(["/u/a_%/"])
    assert selector == ("/u/a_%/",)
    assert is_descendant("/u/a_%/x/", selector[0])
    assert not is_descendant("/u/aXb/x/", selector[0])


# --- separator and boundary semantics --------------------------------------


def test_trailing_separator_prevents_identifier_prefix_collision() -> None:
    """`/u/ab/` must not be reachable from `/u/a/`."""
    assert not parse_scope("/u/a/").is_prefix_of(parse_scope("/u/ab/"))
    assert not is_descendant("/u/ab/", "/u/a/")


def test_ancestor_reads_descendants_but_not_siblings() -> None:
    root = parse_scope("/u/alice/")
    assert root.is_prefix_of(parse_scope("/u/alice/projects/apollo/"))
    assert root.is_prefix_of(root)
    assert not root.is_prefix_of(parse_scope("/u/bob/"))


def test_nested_scope_prefixes_are_consistent_at_every_depth() -> None:
    path = parse_scope("/u/alice/projects/apollo/tasks/")
    ancestors = ["/u/alice/", "/u/alice/projects/", "/u/alice/projects/apollo/"]
    for ancestor in ancestors:
        assert path.is_within(parse_scope(ancestor))
    # At each depth, a sibling that shares the name as a string prefix must not match.
    assert not parse_scope("/u/alice/projects/apollo/").is_prefix_of(parse_scope("/u/alice/projects/apollox/"))
    assert not parse_scope("/u/alice/projects/").is_prefix_of(parse_scope("/u/alice/projectsx/"))


def test_strict_ancestor_excludes_equality() -> None:
    path = parse_scope("/u/alice/")
    assert not path.is_strict_ancestor_of(path)
    assert path.is_strict_ancestor_of(parse_scope("/u/alice/x/"))


def test_parent_and_child_are_inverse() -> None:
    parent = parse_scope("/u/alice/")
    assert parent.child("projects").parent == parent
    assert parent.parent is not None
    assert parent.parent.parent is None


# --- validation fails closed ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("/u/alice", "scope_must_end_with_separator"),
        ("u/alice/", "scope_must_start_with_separator"),
        ("", "scope_must_not_be_blank"),
        ("/", "scope_must_have_at_least_one_segment"),
        ("/u//", "scope_must_not_contain_empty_segment"),
        ("/u/alice//x/", "scope_must_not_contain_empty_segment"),
        ("/x/alice/", "scope_must_begin_with_user_namespace"),
        ("/u/alice\n/", "scope_must_not_contain_control_characters"),
        ("/u/alice/Proj/", "scope_segment_charset_invalid"),
        ("/u/alice/../", "scope_segment_charset_invalid"),
    ],
)
def test_malformed_paths_are_rejected_with_static_codes(text: str, code: str) -> None:
    with pytest.raises(ScopeError) as error:
        parse_scope(text)
    assert error.value.code == code


def test_user_id_may_not_contain_separator() -> None:
    with pytest.raises(ScopeError) as error:
        user_scope("a/b")
    assert error.value.code == "user_id_must_not_contain_separator"


def test_user_id_may_not_escape_with_relative_reference() -> None:
    for candidate in (".", ".."):
        with pytest.raises(ScopeError):
            user_scope(candidate)


def test_user_id_rejects_nul_and_whitespace() -> None:
    with pytest.raises(ScopeError):
        user_scope("alice\x00")
    with pytest.raises(ScopeError):
        user_scope(" alice")


def test_non_string_input_is_rejected_not_coerced() -> None:
    with pytest.raises(ScopeError):
        parse_scope(123)  # type: ignore[arg-type]


# --- selector sets ---------------------------------------------------------


def test_read_prefixes_preserve_order_and_deduplicate() -> None:
    assert normalize_read_prefixes(["/u/a/", "/u/b/", "/u/a/"]) == ("/u/a/", "/u/b/")


def test_empty_selector_set_is_a_resource_error_not_a_wildcard() -> None:
    """An empty read set must never degrade into 'read everything'."""
    with pytest.raises(ScopeError) as error:
        normalize_read_prefixes([])
    assert error.value.code == "at_least_one_read_prefix_required"


def test_selector_cap_is_enforced_rather_than_truncated() -> None:
    too_many = [f"/u/u{index}/" for index in range(MAX_READ_SCOPE_PREFIXES + 1)]
    with pytest.raises(ScopeError) as error:
        normalize_read_prefixes(too_many)
    assert error.value.code == "read_prefix_limit_exceeded"


def test_bare_string_is_rejected_as_a_selector_set() -> None:
    with pytest.raises(ScopeError):
        normalize_read_prefixes("/u/a/")  # type: ignore[arg-type]


# --- the SQL predicate -----------------------------------------------------


def _render(composed: object) -> str:
    """Render a Composed without a connection (identifiers are double-quoted)."""
    return composed.as_string(None)  # type: ignore[attr-defined]


def test_subtree_predicate_never_emits_like() -> None:
    clause, params = subtree_predicate("scope_path", ["/u/a_b/"])
    text = _render(clause)
    assert "LIKE" not in text.upper()
    assert "starts_with" in text
    assert params == ("/u/a_b/",)


def test_subtree_predicate_binds_each_prefix_separately() -> None:
    clause, params = subtree_predicate("v.scope_path", ["/u/a/", "/u/b/"])
    text = _render(clause)
    assert text.count("%s") == 2
    assert text.startswith("(") and text.endswith(")")
    assert '"v"."scope_path"' in text
    assert params == ("/u/a/", "/u/b/")


def test_subtree_predicate_rejects_untrusted_column() -> None:
    for column in ("scope_path) OR (1=1", "a.b.c", "", "1col"):
        with pytest.raises(ScopeError):
            subtree_predicate(column, ["/u/a/"])


def test_scope_path_is_hashable_and_orderable() -> None:
    paths = {parse_scope("/u/a/"), parse_scope("/u/a/"), parse_scope("/u/b/")}
    assert len(paths) == 2
    assert sorted(paths) == [parse_scope("/u/a/"), parse_scope("/u/b/")]
    assert isinstance(parse_scope("/u/a/"), ScopePath)
