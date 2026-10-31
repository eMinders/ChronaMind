"""B-03 / V2-D37: provenance never discloses an unreadable scope (I17), adversarially."""

import dataclasses

import pytest

from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.provenance import (
    OpaqueReference,
    ProvenanceError,
    ProvenanceKey,
    ProvenanceSource,
    VisibleReference,
    is_opaque_reference,
    log_fields,
    opaque_handle,
    render_bundle,
    render_reference,
)

KEY = ProvenanceKey(b"k" * 32)
CAROL_PRIVATE = ProvenanceSource("v-carol-7", "/u/carol/health/", "private", "turn", "consolidation")
ALICE_PROJECT = ProvenanceSource("v-alice-3", "/u/alice/projects/b/", "project", "artifact", "summary")


def read_set(caller: str, selectors: list[str], grants: tuple[str, ...] = ()):
    return resolve_read_set(caller, [Selector.of(path) for path in selectors], grants)


def test_unreadable_source_renders_without_scope_id_or_type() -> None:
    bob = read_set("bob", ["/u/bob/"])
    ref = render_reference(CAROL_PRIVATE, caller_user_id="bob", read_set=bob, key=KEY)
    assert isinstance(ref, OpaqueReference)
    rendered = repr(ref) + str(dataclasses.asdict(ref))
    for secret in ("carol", "health", "v-carol-7", "/u/", "turn"):
        assert secret not in rendered, secret
    assert ref.derivation_type == "consolidation"
    assert is_opaque_reference(ref.opaque_ref)


def test_readable_source_renders_in_full() -> None:
    alice = read_set("alice", ["/u/alice/"])
    ref = render_reference(ALICE_PROJECT, caller_user_id="alice", read_set=alice, key=KEY)
    assert ref == VisibleReference("v-alice-3", "/u/alice/projects/b/", "artifact", "summary")


def test_grant_makes_a_shared_source_visible_but_never_a_private_one() -> None:
    bob = read_set("bob", ["/u/bob/", "/u/alice/projects/b/"], grants=("/u/alice/projects/b/",))
    assert render_reference(ALICE_PROJECT, caller_user_id="bob", read_set=bob, key=KEY).readable
    private = dataclasses.replace(ALICE_PROJECT, visibility="private")
    assert not render_reference(private, caller_user_id="bob", read_set=bob, key=KEY).readable


def test_handle_is_stable_per_caller_and_uncorrelatable_across_callers() -> None:
    assert opaque_handle(KEY, "bob", "v-carol-7") == opaque_handle(KEY, "bob", "v-carol-7")  # lineage
    assert opaque_handle(KEY, "bob", "v-carol-7") != opaque_handle(KEY, "dave", "v-carol-7")  # no correlation
    assert opaque_handle(KEY, "bob", "v-carol-7") != opaque_handle(KEY, "bob", "v-carol-8")  # distinct sources
    assert opaque_handle(KEY, "bob", "v-carol-7") != opaque_handle(ProvenanceKey(b"z" * 32), "bob", "v-carol-7")


def test_caller_and_source_boundary_cannot_be_shifted_to_collide() -> None:
    """caller 'a'+source 'bc' must not equal caller 'ab'+source 'c' (separator is NUL)."""
    assert opaque_handle(KEY, "a", "bc") != opaque_handle(KEY, "ab", "c")


def test_wildcard_shaped_scope_does_not_unlock_another_tenant() -> None:
    bob = read_set("bob", ["/u/bob/", "/u/a_b/"], grants=("/u/a_b/",))
    hidden = ProvenanceSource("v-x", "/u/aXb/", "project", "turn", "consolidation")
    assert not render_reference(hidden, caller_user_id="bob", read_set=bob, key=KEY).readable


def test_malformed_source_scope_fails_closed() -> None:
    alice = read_set("alice", ["/u/alice/"])
    broken = dataclasses.replace(ALICE_PROJECT, scope_path="/u/alice")
    assert isinstance(render_reference(broken, caller_user_id="alice", read_set=alice, key=KEY), OpaqueReference)


def test_bundle_mixes_visible_and_opaque_in_order() -> None:
    alice = read_set("alice", ["/u/alice/"])
    bundle = render_bundle([ALICE_PROJECT, CAROL_PRIVATE], caller_user_id="alice", read_set=alice, key=KEY)
    assert [ref.readable for ref in bundle] == [True, False]


def test_opaque_handle_is_not_an_identifier_shape() -> None:
    assert is_opaque_reference(opaque_handle(KEY, "bob", "v1"))
    for value in ("v-carol-7", "opq_", "opq_" + "g" * 32, None, 7):
        assert not is_opaque_reference(value)


def test_log_fields_are_content_free_for_both_kinds() -> None:
    bob = read_set("bob", ["/u/bob/"])
    alice = read_set("alice", ["/u/alice/"])
    for ref in (
        render_reference(CAROL_PRIVATE, caller_user_id="bob", read_set=bob, key=KEY),
        render_reference(ALICE_PROJECT, caller_user_id="alice", read_set=alice, key=KEY),
    ):
        text = str(log_fields(ref))
        assert "/u/" not in text and "v-" not in text


def test_short_or_missing_key_fails_closed_and_is_never_printed() -> None:
    with pytest.raises(ProvenanceError):
        ProvenanceKey(b"short")
    with pytest.raises(ProvenanceError):
        ProvenanceKey("x" * 40)  # type: ignore[arg-type]
    assert "kkkk" not in repr(KEY)


def test_errors_are_static_codes_without_input_echo() -> None:
    with pytest.raises(ProvenanceError) as error:
        opaque_handle(KEY, "bob", "")
    assert error.value.code == "source_id_required"
