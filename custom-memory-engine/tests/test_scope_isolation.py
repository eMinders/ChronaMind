"""MA-011 isolation suite, application layer (no database). The PostgreSQL twin is
``tests/integration/test_scope_isolation.py``; ``E-011`` re-runs the same cases on the real schema.

Parameterised over the matching predicate: the two historical ``LIKE`` designs fail the cases they were
reported for, and ``starts_with`` (``V2-D35``) passes every case.
"""

import itertools

import pytest

from custom_memory_engine.scope.access import (
    Selector,
    acl_readable,
    authorize_write,
    resolve_read_set,
    rls_read_using,
    row_visible,
)
from custom_memory_engine.scope.paths import MAX_READ_SCOPE_PREFIXES, ScopeError, parse_scope
from tests.scope_isolation_cases import CASES, EXPECTED_FAILURES, MATCHERS, ROWS, USERS, Case, evaluate


@pytest.mark.parametrize("matcher", sorted(MATCHERS))
@pytest.mark.parametrize("case", CASES, ids=lambda c: f"case{c.number}")
def test_each_matcher_passes_exactly_the_cases_it_should(matcher: str, case: Case) -> None:
    passes = evaluate(case, MATCHERS[matcher][0]) == case.expected
    assert passes is (case.number not in EXPECTED_FAILURES[matcher]), (matcher, case.name)


def test_flawed_r2_predicate_fails_case_1_and_decided_predicate_passes_all() -> None:
    by_case = {c.number: c for c in CASES}
    leaked = evaluate(by_case[1], MATCHERS["like_without_separator"][0]) - by_case[1].expected
    assert {path for path, _ in leaked} == {"/u/42/", "/u/4x/", "/u/40/p/a/"}
    assert all(evaluate(c, MATCHERS["starts_with"][0]) == c.expected for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"case{c.number}")
def test_reference_semantics_return_exactly_the_expected_rows(case: Case) -> None:
    read_set = resolve_read_set(case.caller, case.selectors, case.grants)
    assert not read_set.denied, "every case's selectors are authorized for its caller"
    assert frozenset(r for r in ROWS if row_visible(read_set, *r)) == case.expected


# --- case 6 (grammar), case 7 (limit) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["/u/4", "u/4/", "/u//4/", "/u/4/../", "/u/4/P/", "/u/4/é/", "/u/4/a_b/", "/u/4/a%/", "/u/4/a b/"]
)
def test_malformed_paths_are_validation_errors(bad: str) -> None:
    with pytest.raises(ScopeError):
        parse_scope(bad)
    with pytest.raises(ScopeError):
        Selector.subtree(bad)


def test_sixteen_selectors_are_accepted_and_seventeen_are_a_resource_error_returning_nothing() -> None:
    selectors = [Selector.subtree(f"/u/4/p/s{i}/") for i in range(MAX_READ_SCOPE_PREFIXES + 1)]
    assert len(resolve_read_set("4", selectors[:16], ()).entries) == 16
    with pytest.raises(ScopeError) as error:
        resolve_read_set("4", selectors, ())
    assert error.value.code == "read_prefix_limit_exceeded"


# --- case 8: the two layers, each tested alone ------------------------------------------------------------


def test_application_layer_alone_denies_a_cross_tenant_selector() -> None:
    read_set = resolve_read_set("4", [Selector.subtree("/u/42/")], ())
    assert read_set.empty and read_set.denied[0].reason == "no_active_grant"
    assert not any(row_visible(read_set, *row) for row in ROWS)


def test_rls_reference_alone_reads_only_the_acl_whatever_the_selectors() -> None:
    readable = {row for row in ROWS if acl_readable("4", (), *row)}
    assert readable == {r for r in ROWS if r[0].startswith("/u/4/")}
    granted = {row for row in ROWS if acl_readable("4", ("/u/42/",), *row)} - readable
    assert granted == {("/u/42/", "shared")}, "a grant never exposes private rows"


@pytest.mark.parametrize("caller", ["", "4/p", "..", None, "4\n"])
def test_rls_reference_fails_closed_on_unusable_identity(caller: object) -> None:
    assert not any(acl_readable(caller, (), *row) for row in ROWS)


def test_every_application_read_set_is_contained_in_the_acl() -> None:
    """Property, exhaustive over the universe: the application layer never exceeds the RLS layer."""
    paths = sorted({p for p, _ in ROWS} | {"/u/4/p/", "/u/42/p/"})
    visibility_sets = [("private", "project", "shared"), ("project", "shared"), ("private",), ("shared",)]
    grant_sets = [(), ("/u/42/",), ("/u/42/", "/u/40/p/")]
    checked = 0
    for caller, grants, path, visibilities, mode in itertools.product(
        USERS, grant_sets, paths, visibility_sets, ("subtree", "exact")
    ):
        read_set = resolve_read_set(caller, [Selector.of(path, visibilities, mode)], grants)
        for row in ROWS:
            if row_visible(read_set, *row):
                assert acl_readable(caller, grants, *row), (caller, grants, path, visibilities, mode, row)
                checked += 1
    assert checked > 100


# --- write rule (§5 rule 1) --------------------------------------------------------------------------------


def test_write_into_own_namespace_is_authorized_at_every_visibility() -> None:
    for visibility in ("private", "project", "shared"):
        assert authorize_write("4", "/u/4/p/a/", visibility, ()).text == "/u/4/p/a/"


@pytest.mark.parametrize(
    ("scope", "visibility", "grants"),
    [
        ("/u/42/", "project", ()),  # no grant
        ("/u/42/", "private", ("/u/42/",)),  # grant never covers private
        ("/u/42/p/", "project", ("/u/42/q/",)),  # outside the grant
        ("/u/40/", "shared", ("/u/4/",)),  # prefix sibling of a grant
        ("/u/4", "project", ()),  # malformed
        ("/u/4/", "secret", ()),  # unknown visibility
    ],
)
def test_write_the_caller_could_not_read_back_is_forbidden(scope: str, visibility: str, grants: tuple) -> None:
    with pytest.raises(ScopeError) as error:
        authorize_write("4", scope, visibility, grants)
    assert error.value.code == "forbidden"


def test_write_into_a_granted_project_scope_is_authorized() -> None:
    assert authorize_write("4", "/u/42/p/", "project", ("/u/42/",)).text == "/u/42/p/"


def test_selector_modes_are_validated() -> None:
    with pytest.raises(ScopeError):
        Selector.of("/u/4/", ("private",), "prefix")
    assert Selector.exact("/u/4/").mode == "exact"
    assert Selector.with_visibility("/u/4/", ("project",)).visibilities == frozenset({"project"})


def test_rls_expression_qualifies_row_columns_and_rejects_untrusted_names() -> None:
    text = repr(rls_read_using("memory_version"))
    assert "Identifier('memory_version', 'scope_path')" in text
    assert "Identifier('memory_version', 'visibility')" in text
    assert "LIKE" not in text.upper()
    for bad in ("t; DROP TABLE x", "a.b", ""):
        with pytest.raises(ScopeError):
            rls_read_using(bad)
