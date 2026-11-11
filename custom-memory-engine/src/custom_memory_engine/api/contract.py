"""The canonical public API contract as data (V2-D39, completed by V2-D46 and V2-D47).

``ARCHITECTURE_V2.md`` §8.1 / ``PLAN/V2/API_V2.md`` are the enumeration: "every operation a tier
needs appears here, and nothing outside it exists". ``V2-D39`` made them canonical (23
operations, superseding the ``V2-D07`` five-call draft). ``V2-D46`` closed the gaps other
documents relied on (``dissent``, ``create_entity``, ``register_method`` and the ``Scope``
class), giving 29 operations, 18 of them Tier B. ``V2-D47`` fixed the tier model (``MA-015``):
two tiers, and ``admin`` as a role *within* Tier C.

This module is that enumeration in executable form. It is a **specification**, not runtime
enforcement: tests assert it equals both documents and that the structural rules below hold.
Runtime enforcement is built by the W1+ tasks that implement each operation (``E-014``-``E-017``,
``A-017``), which must call :func:`authorize` and honour every ``conditions`` code.

Structural rules (each one a code returned by :func:`contract_violations`):

* TB1 no Tier B operation deletes;
* TB2 no Tier B operation adjudicates (``adjudicate``, ``dissent``);
* TB3 every operation that creates ``asserted`` requires evidence;
* TB4 nothing moves to ``asserted`` except Tier C adjudication; ``inferred → asserted`` does not exist;
* TB5 a Tier B lifecycle change applies to the caller's own method lineage only;
* TB6 Tier B has ``infer`` and ``refute``;
* TB7 every Tier B operation that creates an artifact requires a writable scope;
* TB8 every operation is assigned to at least one tier;
* TB9 Tier B has no Maintain, Export or Scope operation (it can neither change access nor bulk-read);
* TB10 a Tier B operation that names existing artifacts requires them to be readable;
* admin-only operations are Tier C operations.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from custom_memory_engine.model.axes import AxisError, Lifecycle, require_transition

type Tier = Literal["B", "C"]
type Capability = Literal[
    "Ingest", "Read", "Write (grounded)", "Write (inferred)", "Structure",
    "Adjudicate", "Maintain", "Delete", "Export", "Observe", "Scope",
]  # fmt: skip
type Condition = Literal[
    "evidence_required",  # non-empty SourceRef evidence (TB3)
    "own_lineage_only",  # Tier B: only artifacts its own method lineage produced (TB5)
    "scope_writable",  # the target scope passes scope.access.authorize_write (TB7)
    "targets_readable",  # every named artifact/entity/association is in the caller's read set (TB10)
    "registered_method",  # the method is in the append-only METHOD registry (I3)
    "namespace_owner_only",  # the scope lies in the caller's own /u/<caller>/ namespace (V2-D36)
    "grounded_endpoint_protected",  # a Tier B `contradicts` edge never demotes a grounded artifact (V2-D48)
]

CONDITIONS: frozenset[str] = frozenset(Condition.__value__.__args__)
UNIVERSAL_ERRORS = frozenset({"forbidden", "validation_error"})
TIER_B_EXCLUDED_CAPABILITIES: frozenset[str] = frozenset({"Adjudicate", "Maintain", "Delete", "Export", "Scope"})


class ContractError(PermissionError):
    """Static code. ``forbidden`` is returned for unknown and unpermitted alike."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    capability: Capability
    tiers: frozenset[Tier]
    inputs: tuple[str, ...]
    output: str
    errors: frozenset[str]
    creates: frozenset[Lifecycle] = frozenset()
    transitions: frozenset[tuple[Lifecycle, Lifecycle]] = frozenset()
    deletes: bool = False
    admin_only: bool = False
    conditions: frozenset[Condition] = field(default_factory=frozenset)

    @property
    def requires_evidence(self) -> bool:
        return "evidence_required" in self.conditions


def _op(
    name: str,
    capability: Capability,
    tiers: str,
    inputs: tuple[str, ...],
    output: str,
    errors: tuple[str, ...] = (),
    conditions: tuple[Condition, ...] = (),
    **effects: object,
) -> Operation:
    return Operation(
        name,
        capability,
        frozenset(tiers),  # type: ignore[arg-type]
        inputs,
        output,
        UNIVERSAL_ERRORS | frozenset(errors),
        conditions=frozenset(conditions),
        **effects,  # type: ignore[arg-type]
    )


OPERATIONS: tuple[Operation, ...] = (
    _op("ingest_episode", "Ingest", "BC", ("scope", "source", "content", "event_time", "idempotency_key"), "Receipt",
        conditions=("scope_writable",), creates=frozenset({"observed"})),
    _op("ingest_artifact", "Ingest", "BC", ("scope", "kind", "candidate", "evidence", "idempotency_key"),
        "Receipt | rejected", ("rejected",), ("scope_writable", "evidence_required"),
        creates=frozenset({"asserted", "contested"})),
    _op("query", "Read", "BC", ("request",), "QueryResult", ("resource_limit",)),
    _op("refine", "Read", "BC", ("result_id", "instruction"), "QueryResult", conditions=("targets_readable",)),
    _op("get", "Read", "BC", ("artifact_id",), "Artifact", conditions=("targets_readable",)),
    _op("provenance", "Read", "BC", ("artifact_id", "depth"), "ProvenanceView", conditions=("targets_readable",)),
    _op("assert", "Write (grounded)", "BC", ("scope", "kind", "content", "evidence", "event_range", "idempotency_key"),
        "Receipt | rejected", ("rejected",), ("scope_writable", "evidence_required"),
        creates=frozenset({"asserted", "contested"})),
    _op("retract_own", "Write (grounded)", "BC", ("artifact_id", "reason"), "Receipt", ("precondition_failed",),
        ("own_lineage_only", "targets_readable"),
        transitions=frozenset({("asserted", "retired"), ("observed", "retired"), ("inferred", "retired")})),
    _op("infer", "Write (inferred)", "B", ("scope", "method", "sources", "statement", "predicate", "ledger",
        "idempotency_key"), "Receipt | rejected", ("rejected",),
        ("scope_writable", "registered_method", "targets_readable"), creates=frozenset({"inferred"})),
    _op("verify", "Write (inferred)", "B", ("artifact_id", "verification"), "Receipt", ("precondition_failed",),
        ("own_lineage_only", "targets_readable"), transitions=frozenset({("inferred", "inferred")})),
    _op("refute", "Write (inferred)", "B", ("artifact_id", "counter_evidence", "reason"), "Receipt",
        ("precondition_failed",), ("own_lineage_only", "targets_readable", "evidence_required"),
        transitions=frozenset({("inferred", "contested"), ("inferred", "retired")})),
    _op("link", "Structure", "BC", ("from", "to", "type", "weight"), "AssociationId", ("resource_limit",),
        ("targets_readable", "grounded_endpoint_protected")),
    _op("unlink", "Structure", "BC", ("association_id",), "Receipt", conditions=("targets_readable",)),
    _op("create_entity", "Structure", "BC", ("scope", "entity_type", "label", "idempotency_key"), "EntityId",
        conditions=("scope_writable",)),
    _op("link_entity", "Structure", "BC", ("entity_a", "entity_b", "relation"), "Receipt",
        conditions=("targets_readable",)),
    _op("record_outcome", "Structure", "BC", ("procedure_id", "outcome", "cost", "episode_ref"), "OutcomeStats",
        ("precondition_failed",), ("targets_readable",)),
    _op("adjudicate", "Adjudicate", "C", ("artifact_id", "decision", "note"), "Receipt", ("precondition_failed",),
        ("targets_readable",), transitions=frozenset({("contested", "asserted"), ("contested", "retired")})),
    _op("dissent", "Adjudicate", "C", ("artifact_id", "note"), "Receipt", ("precondition_failed",),
        ("targets_readable",), transitions=frozenset({("inferred", "retired")})),
    _op("purge_working", "Maintain", "C", ("scope",), "PurgeReceipt", conditions=("scope_writable",), deletes=True,
        admin_only=True),
    _op("expire", "Maintain", "C", ("scope", "as_of"), "Receipt", conditions=("scope_writable",), deletes=True,
        admin_only=True),
    _op("rebuild_index", "Maintain", "C", ("index",), "JobId", admin_only=True),
    _op("register_method", "Maintain", "C", ("name", "version", "params_schema_hash", "description",
        "idempotency_key"), "MethodId", ("precondition_failed",), admin_only=True),
    _op("delete", "Delete", "C", ("scope", "target_ref", "deletion_epoch"), "DeletionReceipt",
        ("retryable_conflict",), ("scope_writable",), deletes=True),
    _op("export", "Export", "C", ("selectors",), "ExportSnapshot", ("resource_limit",)),
    _op("create_scope", "Scope", "C", ("parent", "segment", "kind", "idempotency_key"), "ScopePath",
        conditions=("namespace_owner_only",)),
    _op("grant_access", "Scope", "C", ("scope", "principal", "idempotency_key"), "GrantId",
        conditions=("namespace_owner_only",)),
    _op("revoke_access", "Scope", "C", ("grant_id",), "Receipt", ("precondition_failed",),
        ("namespace_owner_only",)),
    _op("stats", "Observe", "BC", ("artifact_id",), "Stats", conditions=("targets_readable",)),
    _op("cost", "Observe", "BC", ("window",), "CostReport"),
)  # fmt: skip

BY_NAME: dict[str, Operation] = {operation.name: operation for operation in OPERATIONS}

#: Inputs that name an existing artifact, association, entity or result.
_TARGET_INPUTS = frozenset(
    {"artifact_id", "result_id", "association_id", "from", "to", "entity_a", "entity_b", "procedure_id", "sources"}
)


def authorize(operation_name: object, tier: object, *, admin: bool = False) -> Operation:
    """Return the operation if ``tier`` (with the ``admin`` role, if held) may call it; else ``forbidden``.

    Unknown operations (including the retired five-call name ``retract``) are ``forbidden`` exactly
    like unpermitted ones, so the error reveals nothing. ``admin`` is a role within Tier C: it is
    ignored for Tier B, and an admin-only operation needs it.
    """
    operation = BY_NAME.get(operation_name) if isinstance(operation_name, str) else None
    if operation is None or tier not in ("B", "C") or tier not in operation.tiers:
        raise ContractError("forbidden")
    if operation.admin_only and not (tier == "C" and admin is True):
        raise ContractError("forbidden")
    return operation


def contract_violations(operations: Iterable[Operation] = OPERATIONS) -> list[str]:
    """Every structural-rule violation in ``operations``, as static codes (empty when valid)."""
    ops = tuple(operations)
    found: list[str] = []
    names = [op.name for op in ops]
    if len(set(names)) != len(names):
        found.append("duplicate_operation")
    by_name = {op.name: op for op in ops}
    for name in ("infer", "refute"):
        if name not in by_name or "B" not in by_name[name].tiers:
            found.append(f"tier_b_must_have_{name}")  # TB6
    for op in ops:
        if not op.inputs or not op.output or not UNIVERSAL_ERRORS <= op.errors:
            found.append(f"incomplete_operation_contract:{op.name}")
        if not op.tiers or not op.tiers <= {"B", "C"}:
            found.append(f"unassigned_operation:{op.name}")  # TB8
        if not op.conditions <= CONDITIONS:
            found.append(f"unknown_condition:{op.name}")
        try:
            for source, target in op.transitions:
                require_transition(source, target)
            for target in op.creates:
                require_transition(None, target)
        except AxisError:
            found.append(f"illegal_transition:{op.name}")
        if "asserted" in op.creates and not op.requires_evidence:
            found.append(f"asserted_without_evidence:{op.name}")  # TB3
        if any(t == "asserted" for _, t in op.transitions) and (op.capability != "Adjudicate" or "B" in op.tiers):
            found.append(f"promotion_to_asserted:{op.name}")  # TB4
        if any(s == "inferred" and t == "asserted" for s, t in op.transitions):
            found.append(f"inferred_to_asserted:{op.name}")  # TB4
        if op.admin_only and op.tiers != {"C"}:
            found.append(f"admin_only_outside_tier_c:{op.name}")
        if "B" not in op.tiers:
            continue
        if op.deletes:
            found.append(f"tier_b_may_not_delete:{op.name}")  # TB1
        if op.capability == "Adjudicate":
            found.append(f"tier_b_may_not_adjudicate:{op.name}")  # TB2
        if op.capability in TIER_B_EXCLUDED_CAPABILITIES:
            found.append(f"tier_b_excluded_capability:{op.name}")  # TB9
        if op.transitions and "own_lineage_only" not in op.conditions:
            found.append(f"tier_b_lifecycle_change_without_own_lineage:{op.name}")  # TB5
        if op.creates and "scope_writable" not in op.conditions:
            found.append(f"tier_b_create_without_writable_scope:{op.name}")  # TB7
        if _TARGET_INPUTS & set(op.inputs) and "targets_readable" not in op.conditions:
            found.append(f"tier_b_unreadable_target:{op.name}")  # TB10
        if op.capability == "Write (inferred)" and op.creates and "registered_method" not in op.conditions:
            found.append(f"tier_b_infer_without_registered_method:{op.name}")
    return found


def validate_contract(operations: Iterable[Operation] = OPERATIONS) -> None:
    """Raise ``ContractError`` with the first violation's code; return when the contract is valid."""
    violations = contract_violations(operations)
    if violations:
        raise ContractError(violations[0])


__all__ = [
    "BY_NAME",
    "CONDITIONS",
    "OPERATIONS",
    "TIER_B_EXCLUDED_CAPABILITIES",
    "UNIVERSAL_ERRORS",
    "Capability",
    "Condition",
    "ContractError",
    "Operation",
    "Tier",
    "authorize",
    "contract_violations",
    "validate_contract",
]
