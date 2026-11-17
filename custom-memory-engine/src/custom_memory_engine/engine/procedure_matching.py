"""Preconditions matching: is this procedure applicable to this request? (E-030, ``ARCHITECTURE_V2.md`` §6, §9.)

A pure function of its arguments: no database, no clock, no profile. It answers **applicability** and nothing else. It
never runs a step, checks a side effect or sequences anything (``ARCHITECTURE_V2.md`` §6: "the engine stores and
retrieves procedures; the agent executes them").

**Semantics.** A procedure's preconditions are a conjunction: the procedure is applicable when *every* condition holds.

* ``scope_within(S)`` holds when the request names a scope and that scope lies in ``S``'s subtree (literal canonical
  prefix, the rule of ``scope.paths``: ``/u/a/`` contains ``/u/a/b/`` and not ``/u/ab/``). A request that names no
  scope does **not** satisfy it: missing information never makes a procedure applicable.
* ``exists(M)`` holds when at least one artifact in the set has every field of ``M`` equal to its value.
* ``absent(M)`` holds when no artifact in the set does. With an empty artifact set it holds.

**No preconditions** (an empty list, or a procedure stored without a shape) is the empty conjunction, which is true:
the procedure is **always applicable**. That keeps every procedure written before E-030 working (``§5.4``).

**Where it runs (``§5.3``).** Matching is a *filter*, so it runs before routing: it removes a non-applicable
procedure from the candidate set. It returns a boolean, never a score, so it cannot reorder anything, and it never
moves an artifact between channels. Routing (``channel_of``) and the ``V2-D75`` outcome ranking then run unchanged on
the survivors: an applicable procedure lands exactly where it would have without matching.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from custom_memory_engine.engine.procedure_shape import NO_SHAPE, Precondition, ProcedureShape
from custom_memory_engine.scope.paths import parse_scope


@dataclass(frozen=True, slots=True)
class ApplicabilityRequest:
    """The part of a retrieval request preconditions are matched against: the canonical scope it names, if any."""

    scope: str | None = None

    def __post_init__(self) -> None:
        # the literal-prefix rule of `scope_within` is exact only for canonical paths: refuse anything else here
        if self.scope is not None:
            parse_scope(self.scope)


@dataclass(frozen=True, slots=True)
class ArtifactFacts:
    """The axes of one artifact in the set a request retrieved, as ``exists``/``absent`` see them."""

    kind: str
    lifecycle: str
    facet: str | None = None
    predicate: str | None = None


def _selected(condition: Precondition, artifact: ArtifactFacts) -> bool:
    values = {
        "kind": artifact.kind,
        "lifecycle": artifact.lifecycle,
        "facet": artifact.facet,
        "predicate": artifact.predicate,
    }
    return all(values[field] == value for field, value in condition.match)


def _holds(condition: Precondition, request: ApplicabilityRequest, artifacts: Sequence[ArtifactFacts]) -> bool:
    if condition.op == "scope_within":
        return (
            request.scope is not None
            and condition.scope is not None
            and request.scope.startswith(condition.scope)  # canonical paths: string prefix == segment prefix
        )
    found = any(_selected(condition, artifact) for artifact in artifacts)
    return found if condition.op == "exists" else not found


def matches(
    preconditions: Iterable[Precondition], request: ApplicabilityRequest, artifacts: Iterable[ArtifactFacts]
) -> bool:
    """Whether a procedure with ``preconditions`` is applicable to ``request`` over ``artifacts``.

    The conjunction of every condition; ``True`` for none. Pure and deterministic.
    """
    return _matches(preconditions, request, tuple(artifacts))


def _matches(
    preconditions: Iterable[Precondition], request: ApplicabilityRequest, retrieved: Sequence[ArtifactFacts]
) -> bool:
    return all(_holds(condition, request, retrieved) for condition in preconditions)


def applicable(
    procedure_ids: Sequence[str],
    shapes: Mapping[str, ProcedureShape],
    request: ApplicabilityRequest,
    artifacts: Iterable[ArtifactFacts],
) -> tuple[str, ...]:
    """The applicable subset of ``procedure_ids``, in their given order. A procedure with no shape is applicable.

    A filter: it only removes ids. It never adds, reorders or reroutes one, which is what lets it run before routing
    and before the ``V2-D75`` ranking without changing either.
    """
    retrieved = tuple(artifacts)
    return tuple(
        procedure_id
        for procedure_id in procedure_ids
        if _matches(shapes.get(procedure_id, NO_SHAPE).preconditions, request, retrieved)
    )


__all__ = ["ApplicabilityRequest", "ArtifactFacts", "applicable", "matches"]
