"""B-04 / V2-D39, V2-D46, V2-D47: the executable API contract equals ARCHITECTURE_V2 §8.1 and API_V2.md."""

import re
from pathlib import Path

import pytest

from custom_memory_engine.api.contract import BY_NAME, OPERATIONS, ContractError, authorize, validate_contract

PLAN = Path(__file__).resolve().parents[1] / "PLAN/V2"


def architecture_matrix() -> dict[str, tuple[str, frozenset[str]]]:
    """Parse §8.1: class | operations | Tier B | Tier C."""
    text = (PLAN / "ARCHITECTURE_V2.md").read_text(encoding="utf-8")
    section = text[text.index("### 8.1") : text.index("### 8.2")]
    matrix: dict[str, tuple[str, frozenset[str]]] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[0].startswith("**"):
            continue
        capability = cells[0].strip("*")
        tiers = frozenset(t for t, cell in (("B", cells[2]), ("C", cells[3])) if "✓" in cell)
        for name in re.findall(r"`([a-z_]+)`", cells[1]):
            matrix[name] = (capability, tiers)
    return matrix


def api_rows() -> dict[str, list[str]]:
    """Parse API_V2.md §3: # | class | operation | Tier B | Tier C | Admin | conditions."""
    text = (PLAN / "API_V2.md").read_text(encoding="utf-8")
    section = text[text.index("## 3.") : text.index("### 3.1")]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[0].isdigit():
            assert cells[2].strip("`") not in rows, cells[2]
            rows[cells[2].strip("`")] = cells
    return rows


def api_matrix() -> dict[str, tuple[str, frozenset[str]]]:
    """Tiers as §8.1 counts them: Tier C includes the admin role."""
    return {
        name: (cells[1], frozenset(t for t, cell in (("B", cells[3]), ("C", cells[5])) if "✓" in cell))
        for name, cells in api_rows().items()
    }


def api_signatures() -> dict[str, tuple[str, ...]]:
    """Parse every ``name(ctx, a, b?, …)`` signature in API_V2.md §4."""
    text = (PLAN / "API_V2.md").read_text(encoding="utf-8")
    section = text[text.index("## 4.") : text.index("## 5.")]
    signatures: dict[str, tuple[str, ...]] = {}
    for name, args in re.findall(r"\*\*`([a-z_]+)\(ctx,?\s*([^)]*)\)", section.replace("\n", " ")):
        parts = [part.strip() for part in re.split(r",(?![^{]*\})", args) if part.strip()]
        signatures[name] = tuple(re.split(r"[:?|\s]", part, maxsplit=1)[0] for part in parts)
    return signatures


def registry() -> dict[str, tuple[str, frozenset[str]]]:
    return {op.name: (op.capability, frozenset(op.tiers)) for op in OPERATIONS}


def test_contract_is_structurally_valid() -> None:
    validate_contract()


def test_twenty_nine_operations_eighteen_for_tier_b() -> None:
    assert len(OPERATIONS) == 29
    assert sum("B" in op.tiers for op in OPERATIONS) == 18


def test_registry_equals_architecture_section_8_1() -> None:
    assert registry() == architecture_matrix()


def test_registry_equals_api_v2_tier_matrix() -> None:
    assert registry() == api_matrix()


def test_admin_column_and_conditions_equal_the_registry() -> None:
    for name, cells in api_rows().items():
        op = BY_NAME[name]
        assert ("✓" in cells[4]) is ("C" in op.tiers and not op.admin_only), name
        assert frozenset(re.findall(r"`([a-z_]+)`", cells[6])) == op.conditions, name


def test_registry_inputs_equal_every_api_v2_signature() -> None:
    signatures = api_signatures()
    assert set(signatures) == set(BY_NAME), set(signatures) ^ set(BY_NAME)
    for name, inputs in signatures.items():
        assert BY_NAME[name].inputs == inputs, name


def test_retract_does_not_exist_and_retract_own_does() -> None:
    """The five-call list named ``retract``; the approved surface has only ``retract_own``."""
    assert "retract" not in BY_NAME
    with pytest.raises(ContractError) as error:
        authorize("retract", "B")
    assert error.value.code == "forbidden"
    assert authorize("retract_own", "B").name == "retract_own"


def test_v2_d07_five_calls_remain_available_to_tier_b_except_the_nonexistent_one() -> None:
    for name in ("query", "infer", "verify", "stats"):
        authorize(name, "B")


@pytest.mark.parametrize("name", ["delete", "adjudicate", "purge_working", "expire", "rebuild_index", "export"])
def test_tier_b_is_forbidden_from_destructive_and_adjudicating_operations(name: str) -> None:
    with pytest.raises(ContractError):
        authorize(name, "B")
    authorize(name, "C", admin=True)


def test_tier_c_cannot_run_inference_operations() -> None:
    for name in ("infer", "verify", "refute"):
        with pytest.raises(ContractError):
            authorize(name, "C", admin=True)


def test_tb4_only_adjudication_moves_an_existing_artifact_to_asserted() -> None:
    promoting = {op.name for op in OPERATIONS if any(target == "asserted" for _, target in op.transitions)}
    assert promoting == {"adjudicate"}
    assert all(("inferred", "asserted") not in op.transitions for op in OPERATIONS)


def test_tb3_grounded_writes_require_evidence() -> None:
    for op in OPERATIONS:
        if "asserted" in op.creates:
            assert op.requires_evidence, op.name


def test_unknown_operation_and_unknown_tier_fail_closed_identically() -> None:
    for name, tier in (("drop_everything", "C"), ("query", "A"), (None, "B")):
        with pytest.raises(ContractError) as error:
            authorize(name, tier)
        assert error.value.code == "forbidden"
