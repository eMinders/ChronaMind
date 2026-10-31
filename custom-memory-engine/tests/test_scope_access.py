"""B-02 / V2-D36: read-set resolution over scope grants is fail-closed."""

import pytest

from custom_memory_engine.scope.access import ReadSet, Selector, read_predicate, resolve_read_set, row_visible
from custom_memory_engine.scope.paths import MAX_READ_SCOPE_PREFIXES, ScopeError


def resolve(caller: str, selectors: list[Selector], grants: tuple[str, ...] = ()) -> ReadSet:
    return resolve_read_set(caller, selectors, grants)


def test_own_namespace_is_always_readable_at_every_visibility() -> None:
    read = resolve("alice", [Selector.of("/u/alice/projects/")])
    assert [(e.path, sorted(e.visibilities)) for e in read.entries] == [
        ("/u/alice/projects/", ["private", "project", "shared"])
    ]
    assert read.denied == ()


def test_missing_grant_denies_cross_user_selector() -> None:
    read = resolve("bob", [Selector.of("/u/alice/projects/apollo/")])
    assert read.empty
    assert [(d.path, d.reason) for d in read.denied] == [("/u/alice/projects/apollo/", "no_active_grant")]


def test_grant_authorizes_its_subtree_but_never_private() -> None:
    read = resolve("bob", [Selector.of("/u/alice/projects/apollo/")], ("/u/alice/projects/apollo/",))
    assert [(e.path, sorted(e.visibilities)) for e in read.entries] == [
        ("/u/alice/projects/apollo/", ["project", "shared"])
    ]


def test_private_only_cross_user_selector_is_denied() -> None:
    read = resolve("bob", [Selector.of("/u/alice/projects/apollo/", ["private"])], ("/u/alice/projects/apollo/",))
    assert read.empty
    assert read.denied[0].reason == "private_not_readable_across_users"


def test_selector_broader_than_the_grant_is_denied_not_narrowed() -> None:
    read = resolve("bob", [Selector.of("/u/alice/")], ("/u/alice/projects/apollo/",))
    assert read.empty and read.denied[0].reason == "no_active_grant"


def test_wildcard_shaped_grant_does_not_cover_a_sibling_tenant() -> None:
    """B-01 at the authorization layer: a grant on /u/a_b/ never covers /u/aXb/."""
    read = resolve("bob", [Selector.of("/u/aXb/p/")], ("/u/a_b/",))
    assert read.empty


def test_prefix_sibling_is_not_covered_by_a_grant() -> None:
    read = resolve("bob", [Selector.of("/u/alice/projects/apollox/")], ("/u/alice/projects/apollo/",))
    assert read.empty


def test_empty_and_oversized_selector_sets_are_errors_not_wildcards() -> None:
    with pytest.raises(ScopeError):
        resolve("bob", [])
    with pytest.raises(ScopeError):
        resolve("bob", [Selector.of(f"/u/bob/s{i}/") for i in range(MAX_READ_SCOPE_PREFIXES + 1)])


@pytest.mark.parametrize("bad", ["/u/alice", "/x/alice/", "/u/alice/Proj/", "/u/al\nice/"])
def test_malformed_selector_or_grant_fails_closed(bad: str) -> None:
    with pytest.raises(ScopeError):
        Selector.of(bad)
    with pytest.raises(ScopeError):
        resolve("bob", [Selector.of("/u/bob/")], (bad,))


def test_invalid_visibility_is_rejected() -> None:
    with pytest.raises(ScopeError):
        Selector.of("/u/bob/", ["public"])
    with pytest.raises(ScopeError):
        Selector.of("/u/bob/", [])


def test_private_row_matches_only_the_exact_selector_path() -> None:
    read = resolve("alice", [Selector.of("/u/alice/")])
    assert row_visible(read, "/u/alice/", "private")
    assert not row_visible(read, "/u/alice/projects/", "private")  # rule 3: equality, not subtree
    assert row_visible(read, "/u/alice/projects/", "project")


def test_row_shared_into_a_project_is_not_readable_from_a_sibling_project() -> None:
    read = resolve("bob", [Selector.of("/u/alice/projects/b/")], ("/u/alice/projects/b/",))
    assert not row_visible(read, "/u/alice/projects/a/", "project")
    assert row_visible(read, "/u/alice/projects/b/x/", "shared")


def test_unknown_or_malformed_row_values_fail_closed() -> None:
    read = resolve("alice", [Selector.of("/u/alice/")])
    assert not row_visible(read, "/u/alice/", "public")
    assert not row_visible(read, "not-a-path", "project")


def test_empty_read_set_predicate_is_false() -> None:
    clause, params = read_predicate(ReadSet((), ()), "scope_path", "visibility")
    assert clause.as_string(None) == "false" and params == []


def test_read_predicate_rejects_untrusted_columns() -> None:
    read = resolve("alice", [Selector.of("/u/alice/")])
    with pytest.raises(ScopeError):
        read_predicate(read, "scope_path) OR (true", "visibility")
