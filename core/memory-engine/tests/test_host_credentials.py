"""The token registry and its delegation rule (``V2-D47``, ``V2-D120``), without a database or a socket.

The end-to-end refusals over real HTTP are in ``tests/integration/test_host_e2e_db.py``; this module pins the rules
themselves so a change to one fails in a second rather than after a host start-up.
"""

from __future__ import annotations

import json

import pytest

from custom_memory_engine.api.entry import Credential
from custom_memory_engine.host.credentials import (
    Authenticated,
    CredentialsError,
    DelegationRefused,
    HostCredentials,
)

# Test-only, deliberately low-entropy tokens (16+ characters is the registry's only rule).
SERVICE = "test-token-service-delegating"  # pragma: allowlist secret
OPERATOR = "test-token-operator-tier-c-admin"  # pragma: allowlist secret


def registry() -> HostCredentials:
    return HostCredentials(
        {SERVICE: Credential("chronamind-app", "B"), OPERATOR: Credential("ops", "C", admin=True)},
        delegating={SERVICE},
    )


def caller(token: str) -> Authenticated:
    found = registry().authenticate(f"Bearer {token}")
    assert found is not None
    return found


def test_the_delegate_flag_is_read_from_the_environment_document() -> None:
    raw = json.dumps(
        {
            SERVICE: {"principal": "chronamind-app", "tier": "B", "delegate": True},
            OPERATOR: {"principal": "ops", "tier": "C", "admin": True},
        }
    )
    credentials = HostCredentials.from_environment(raw)
    service = credentials.authenticate(f"Bearer {SERVICE}")
    operator = credentials.authenticate(f"Bearer {OPERATOR}")
    assert service is not None and service.may_delegate
    assert operator is not None and not operator.may_delegate


@pytest.mark.parametrize("flag", ["yes", 1, None])
def test_a_delegate_flag_that_is_not_a_boolean_is_refused_at_start_up(flag: object) -> None:
    raw = json.dumps({SERVICE: {"principal": "chronamind-app", "tier": "B", "delegate": flag}})
    with pytest.raises(CredentialsError) as refused:
        HostCredentials.from_environment(raw)
    assert refused.value.code == "host_token_entry_invalid"


def test_an_admin_token_cannot_be_a_delegating_token() -> None:
    with pytest.raises(CredentialsError) as refused:
        HostCredentials({OPERATOR: Credential("ops", "C", admin=True)}, delegating={OPERATOR})
    assert refused.value.code == "delegating_token_cannot_be_admin"


def test_a_delegating_token_that_is_not_registered_is_refused() -> None:
    with pytest.raises(CredentialsError) as refused:
        HostCredentials({SERVICE: Credential("chronamind-app", "B")}, delegating={"some-other-token-value"})
    assert refused.value.code == "delegating_token_unknown"


def test_a_token_without_the_flag_acts_as_itself_and_cannot_name_anyone() -> None:
    operator = caller(OPERATOR)
    assert registry().acting_credential(operator, None) == Credential("ops", "C", admin=True)
    with pytest.raises(DelegationRefused) as refused:
        registry().acting_credential(operator, "carol")
    assert (refused.value.code, refused.value.detail) == ("forbidden", "delegation_not_permitted")


def test_a_delegating_token_acts_as_the_named_user_with_its_own_tier_and_no_admin() -> None:
    acting = registry().acting_credential(caller(SERVICE), "carol")
    assert acting == Credential("carol", "B", admin=False)


def test_a_delegating_token_must_name_a_user() -> None:
    with pytest.raises(DelegationRefused) as refused:
        registry().acting_credential(caller(SERVICE), None)
    assert (refused.value.code, refused.value.detail) == ("validation_error", "principal_required")


@pytest.mark.parametrize("principal", ["", " carol", "carol ", "a/b", "..", ".", "x\x00y", "x" * 257, 7, ["carol"]])
def test_a_principal_that_could_not_be_a_user_segment_is_invalid(principal: object) -> None:
    with pytest.raises(DelegationRefused) as refused:
        registry().acting_credential(caller(SERVICE), principal)
    assert (refused.value.code, refused.value.detail) == ("validation_error", "principal_invalid")


@pytest.mark.parametrize("principal", ["ops", "chronamind-app"])
def test_a_principal_that_owns_a_token_cannot_be_delegated_to(principal: str) -> None:
    with pytest.raises(DelegationRefused) as refused:
        registry().acting_credential(caller(SERVICE), principal)
    assert (refused.value.code, refused.value.detail) == ("forbidden", "delegation_not_permitted")


def test_the_registry_repr_never_shows_a_token_or_a_principal() -> None:
    shown = repr(registry())
    assert SERVICE not in shown and OPERATOR not in shown and "chronamind-app" not in shown
    assert shown == "HostCredentials(tokens=2)"
