"""The canonical public API contract as data (V2-D39, resolves B-04).

``V2-D07`` listed Tier B as "only: query, infer, verify, retract, stats". ``ARCHITECTURE_V2.md``
§8 says that five-call draft "was incomplete" and that "every operation a tier needs
appears here, and nothing outside it exists": 23 operations, 17 of them Tier B, and no
``retract`` (the operation is ``retract_own``). The decision keeps ``V2-D07``'s invariant
(cognition is a client of the API, never a privileged component) and its three
prohibitions, and makes §8.1 / ``PLAN/V2/API_V2.md`` the enumeration.

This module is that enumeration in executable form. Tests assert it equals both
documents and that the Tier B prohibitions hold structurally:

* TB1 no Tier B operation deletes;
* TB2 no Tier B operation adjudicates;
* TB3 Tier B grounded writes require evidence;
* TB4 no operation moves an existing artifact to ``asserted`` except Tier C
  adjudication of a ``contested`` item; ``inferred → asserted`` does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from custom_memory_engine.model.axes import Lifecycle, require_transition

type Tier = Literal["B", "C"]
type Capability = Literal[
    "Ingest", "Read", "Write (grounded)", "Write (inferred)", "Structure",
    "Adjudicate", "Maintain", "Delete", "Export", "Observe",
]  # fmt: skip

UNIVERSAL_ERRORS = frozenset({"forbidden", "validation_error"})


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
    requires_evidence: bool = False


def _op(
    name: str,
    capability: Capability,
    tiers: str,
    inputs: tuple[str, ...],
    output: str,
    errors: tuple[str, ...] = (),
    **effects: object,
) -> Operation:
    return Operation(
        name,
        capability,
        frozenset(tiers),  # type: ignore[arg-type]
        inputs,
        output,
        UNIVERSAL_ERRORS | frozenset(errors),
        **effects,  # type: ignore[arg-type]
    )


OPERATIONS: tuple[Operation, ...] = (
    _op("ingest_episode", "Ingest", "BC", ("scope", "source", "content", "event_time", "idempotency_key"), "Receipt",
        creates=frozenset({"observed"})),
    _op("ingest_artifact", "Ingest", "BC", ("scope", "kind", "candidate", "evidence", "idempotency_key"),
        "Receipt | rejected", ("rejected",), creates=frozenset({"asserted", "contested"}), requires_evidence=True),
    _op("query", "Read", "BC", ("request",), "QueryResult"),
    _op("refine", "Read", "BC", ("result_id", "instruction"), "QueryResult"),
    _op("get", "Read", "BC", ("artifact_id",), "Artifact"),
    _op("provenance", "Read", "BC", ("artifact_id", "depth"), "ProvenanceView"),
    _op("assert", "Write (grounded)", "BC", ("scope", "kind", "content", "evidence", "event_range", "idempotency_key"),
        "Receipt | rejected", ("rejected",), creates=frozenset({"asserted", "contested"}), requires_evidence=True),
    _op("retract_own", "Write (grounded)", "BC", ("artifact_id", "reason"), "Receipt",
        transitions=frozenset({("asserted", "retired"), ("observed", "retired"), ("inferred", "retired")})),
    _op("infer", "Write (inferred)", "B", ("scope", "method", "sources", "statement", "predicate", "ledger",
        "idempotency_key"), "Receipt | rejected", ("rejected",), creates=frozenset({"inferred"})),
    _op("verify", "Write (inferred)", "B", ("artifact_id", "verification"), "Receipt", ("precondition_failed",),
        transitions=frozenset({("inferred", "inferred")})),
    _op("refute", "Write (inferred)", "B", ("artifact_id", "counter_evidence", "reason"), "Receipt",
        transitions=frozenset({("inferred", "contested"), ("inferred", "retired")}), requires_evidence=True),
    _op("link", "Structure", "BC", ("from", "to", "type", "weight"), "AssociationId", ("resource_limit",)),
    _op("unlink", "Structure", "BC", ("association_id",), "Receipt"),
    _op("link_entity", "Structure", "BC", ("entity_a", "entity_b", "relation"), "Receipt"),
    _op("record_outcome", "Structure", "BC", ("procedure_id", "outcome", "cost", "episode_ref"), "OutcomeStats"),
    _op("adjudicate", "Adjudicate", "C", ("artifact_id", "decision", "note"), "Receipt", ("precondition_failed",),
        transitions=frozenset({("contested", "asserted"), ("contested", "retired")})),
    _op("purge_working", "Maintain", "C", ("scope",), "Receipt", deletes=True),
    _op("expire", "Maintain", "C", ("scope", "as_of"), "Receipt", deletes=True),
    _op("rebuild_index", "Maintain", "C", ("index",), "JobId"),
    _op("delete", "Delete", "C", ("scope", "target_ref", "deletion_epoch"), "DeletionReceipt",
        ("retryable_conflict",), deletes=True),
    _op("export", "Export", "C", ("selectors",), "ExportSnapshot", ("resource_limit",)),
    _op("stats", "Observe", "BC", ("artifact_id",), "Stats"),
    _op("cost", "Observe", "BC", ("window",), "CostReport"),
)  # fmt: skip

BY_NAME: dict[str, Operation] = {operation.name: operation for operation in OPERATIONS}


def authorize(operation_name: object, tier: object) -> Operation:
    """Return the operation if ``tier`` may call it; otherwise raise ``forbidden``.

    Unknown operations (including the retired five-call name ``retract``) are
    ``forbidden`` exactly like unpermitted ones, so the error reveals nothing.
    """
    operation = BY_NAME.get(operation_name) if isinstance(operation_name, str) else None
    if operation is None or tier not in ("B", "C") or tier not in operation.tiers:
        raise ContractError("forbidden")
    return operation


def validate_contract() -> None:
    """Assert the structural invariants; raises on the first violation."""
    if len(BY_NAME) != len(OPERATIONS):
        raise ContractError("duplicate_operation")
    for operation in OPERATIONS:
        if not operation.inputs or not operation.output or not UNIVERSAL_ERRORS <= operation.errors:
            raise ContractError("incomplete_operation_contract")
        for source, target in operation.transitions:
            require_transition(source, target)
        for target in operation.creates:
            require_transition(None, target)
        if "B" in operation.tiers:
            if operation.deletes:
                raise ContractError("tier_b_may_not_delete")  # TB1
            if operation.capability == "Adjudicate":
                raise ContractError("tier_b_may_not_adjudicate")  # TB2
            if operation.capability in ("Write (grounded)", "Ingest") and "asserted" in operation.creates:
                if not operation.requires_evidence:
                    raise ContractError("tier_b_grounded_write_requires_evidence")  # TB3
            if any(target == "asserted" for _, target in operation.transitions):
                raise ContractError("tier_b_may_not_promote_to_asserted")  # TB4


__all__ = [
    "BY_NAME",
    "OPERATIONS",
    "UNIVERSAL_ERRORS",
    "Capability",
    "ContractError",
    "Operation",
    "Tier",
    "authorize",
    "validate_contract",
]
