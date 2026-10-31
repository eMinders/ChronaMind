"""MA-015: the capability-to-tier matrix (``API_V2.md`` §3, ``V2-D47``).

**This checks the specification, not runtime enforcement.** No API exists in W0b; these tests check the
matrix itself (``custom_memory_engine.api.contract``), which ``tests/test_api_contract.py`` holds equal to
``API_V2.md`` §3 and ``ARCHITECTURE_V2.md`` §8.1. Runtime enforcement is built by the tasks that implement
the operations (``E-014``-``E-017`` in W1, ``A-017`` in W7): they must call ``authorize`` and honour every
condition code.

Each rule is tested twice: it holds on the real matrix, and it **fails** with its own code on a
deliberately broken copy, so a rule that silently stopped being checked would be caught.
"""

from dataclasses import replace

import pytest

from custom_memory_engine.api.contract import (
    BY_NAME,
    OPERATIONS,
    ContractError,
    Operation,
    authorize,
    contract_violations,
)
from tests.test_api_contract import api_rows

TIER_B = {op.name for op in OPERATIONS if "B" in op.tiers}


def broken(name: str, **changes: object) -> list[str]:
    """Violations of a copy of the matrix in which ``name`` is changed."""
    return contract_violations(replace(op, **changes) if op.name == name else op for op in OPERATIONS)  # type: ignore[arg-type]


def without(name: str, condition: str) -> frozenset[str]:
    """``name``'s conditions minus ``condition`` (the broken copy for a mutation test)."""
    return frozenset(c for c in BY_NAME[name].conditions if c != condition)


def test_the_real_matrix_has_no_violation() -> None:
    assert contract_violations() == []


def test_tb1_tier_b_cannot_delete() -> None:
    assert not any(BY_NAME[n].deletes for n in TIER_B)
    for name in ("delete", "purge_working", "expire"):
        with pytest.raises(ContractError):
            authorize(name, "B", admin=True)
    assert "tier_b_may_not_delete:delete" in broken("delete", tiers=frozenset({"B", "C"}))


def test_tb2_tier_b_cannot_adjudicate() -> None:
    for name in ("adjudicate", "dissent"):
        with pytest.raises(ContractError):
            authorize(name, "B")
        authorize(name, "C")
    assert "tier_b_may_not_adjudicate:dissent" in broken("dissent", tiers=frozenset({"B", "C"}))


def test_tb3_grounded_writes_require_evidence() -> None:
    for op in OPERATIONS:
        if "asserted" in op.creates:
            assert op.requires_evidence, op.name
    assert "asserted_without_evidence:assert" in broken("assert", conditions=frozenset({"scope_writable"}))


def test_tb4_only_tier_c_adjudication_promotes_to_asserted() -> None:
    assert {op.name for op in OPERATIONS if any(t == "asserted" for _, t in op.transitions)} == {"adjudicate"}
    promote = frozenset({("contested", "asserted")})
    assert "promotion_to_asserted:refute" in broken("refute", transitions=promote)
    assert "promotion_to_asserted:adjudicate" in broken("adjudicate", tiers=frozenset({"B", "C"}))
    illegal = broken("verify", transitions=frozenset({("inferred", "asserted")}))
    assert "illegal_transition:verify" in illegal and "inferred_to_asserted:verify" in illegal


def test_tb5_tier_b_lifecycle_changes_are_own_lineage_only() -> None:
    for name in ("retract_own", "verify", "refute"):
        assert "own_lineage_only" in BY_NAME[name].conditions
    stripped = without("verify", "own_lineage_only")
    assert "tier_b_lifecycle_change_without_own_lineage:verify" in broken("verify", conditions=stripped)


def test_tb6_tier_b_can_infer_and_refute() -> None:
    authorize("infer", "B")
    authorize("refute", "B")
    assert "tier_b_must_have_infer" in broken("infer", tiers=frozenset({"C"}))
    assert "tier_b_must_have_refute" in contract_violations(op for op in OPERATIONS if op.name != "refute")


def test_tb7_tier_b_creation_requires_writable_scope() -> None:
    for name in TIER_B:
        if BY_NAME[name].creates or name == "create_entity":
            assert "scope_writable" in BY_NAME[name].conditions, name
    stripped = without("ingest_episode", "scope_writable")
    assert "tier_b_create_without_writable_scope:ingest_episode" in broken("ingest_episode", conditions=stripped)


def test_tb8_every_operation_is_assigned_and_documented() -> None:
    assert all(op.tiers for op in OPERATIONS)
    assert set(api_rows()) == set(BY_NAME), "matrix and API_V2.md name exactly the same operations"
    assert "unassigned_operation:cost" in broken("cost", tiers=frozenset())
    ghost = Operation("teleport", "Read", frozenset({"B"}), ("x",), "Y", frozenset({"forbidden", "validation_error"}))
    assert "teleport" not in api_rows(), "an operation absent from API_V2.md would fail the equality above"
    assert contract_violations((*OPERATIONS, ghost)) == []  # structurally fine, so only the doc check catches it


def test_tb9_tier_b_has_no_maintain_export_or_scope_operation() -> None:
    for name in ("purge_working", "expire", "rebuild_index", "register_method", "export", "create_scope",
                 "grant_access", "revoke_access"):  # fmt: skip
        with pytest.raises(ContractError):
            authorize(name, "B", admin=True)
    assert "tier_b_excluded_capability:export" in broken("export", tiers=frozenset({"B", "C"}))
    assert "tier_b_excluded_capability:grant_access" in broken("grant_access", tiers=frozenset({"B", "C"}))


def test_tb10_tier_b_targets_must_be_readable() -> None:
    stripped = without("link", "targets_readable")
    assert "tier_b_unreadable_target:link" in broken("link", conditions=stripped)
    assert "tier_b_unreadable_target:stats" in broken("stats", conditions=frozenset())


def test_ad1_admin_only_operations_need_tier_c_and_the_role() -> None:
    admin_only = {op.name for op in OPERATIONS if op.admin_only}
    assert admin_only == {"purge_working", "expire", "rebuild_index", "register_method"}
    for name in admin_only:
        with pytest.raises(ContractError):
            authorize(name, "C")
        authorize(name, "C", admin=True)
    authorize("delete", "C", admin=True)  # an admin may call every Tier C operation
    assert "admin_only_outside_tier_c:rebuild_index" in broken("rebuild_index", tiers=frozenset({"B", "C"}))


def test_admin_flag_must_be_exactly_true() -> None:
    with pytest.raises(ContractError):
        authorize("rebuild_index", "C", admin="yes")  # type: ignore[arg-type]


def test_grounded_endpoint_protection_is_a_link_condition() -> None:
    """C-P4 (V2-D48): the security rule is on the matrix row, so the implementing task cannot miss it."""
    assert "grounded_endpoint_protected" in BY_NAME["link"].conditions
