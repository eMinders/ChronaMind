"""A procedure's ``preconditions`` and ``steps``: the shape, its parser, and its storage (E-030, migration 21).

``ARCHITECTURE_V2.md`` §6 gives a procedure four fields. ``outcome_stats`` and ``derived_from`` exist (E-027); this
module is the other two. The engine stores and retrieves them and matches preconditions
(:mod:`engine.procedure_matching`); it never executes a step.

**The grammar** (the database enforces the same rules in ``guard_procedure_shape``; a test pins the agreement):

* ``preconditions`` is a list of at most :data:`MAX_PRECONDITIONS` conditions, read as a conjunction. Each is one of

  - ``{"op": "scope_within", "scope": <canonical scope path>}``;
  - ``{"op": "exists", "match": M}`` and ``{"op": "absent", "match": M}``, where ``M`` has 1-4 keys from
    ``kind``/``lifecycle``/``facet``/``predicate``, each a value of that axis's closed vocabulary (``model.axes``;
    ``predicate`` is a predicate-key token).

  An empty list is valid and means "always applicable".
* ``steps`` is a list of at most :data:`MAX_STEPS` ordered, non-blank free-text strings (``ARCHITECTURE_V2.md`` §6
  allows free text until an executor exists, and there is none).

:func:`parse_shape` validates once, at the write path, and raises :class:`ProcedureShapeError` with a static code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.model.axes import FACETS, KINDS, LIFECYCLES
from custom_memory_engine.scope.paths import ScopeError, parse_scope

type Operator = Literal["scope_within", "exists", "absent"]
type MatchField = Literal["kind", "lifecycle", "facet", "predicate"]

OPERATORS: tuple[Operator, ...] = ("scope_within", "exists", "absent")
MATCH_FIELDS: tuple[MatchField, ...] = ("kind", "lifecycle", "facet", "predicate")
MAX_PRECONDITIONS = 32
MAX_STEPS = 64
MAX_STEP_LENGTH = 2000
_PREDICATE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_VOCABULARY: dict[str, frozenset[str]] = {
    "kind": frozenset(KINDS),
    "lifecycle": frozenset(LIFECYCLES),
    "facet": frozenset(FACETS),
}


class ProcedureShapeError(ValueError):
    """A shape failed validation. The message is a static code, never the input (it is content)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Precondition:
    """One condition. ``scope`` is set for ``scope_within``; ``match`` (sorted field/value pairs) for the others."""

    op: Operator
    scope: str | None = None
    match: tuple[tuple[MatchField, str], ...] = ()

    def to_json(self) -> dict[str, object]:
        if self.op == "scope_within":
            return {"op": self.op, "scope": self.scope}
        return {"op": self.op, "match": dict(self.match)}


@dataclass(frozen=True, slots=True)
class ProcedureShape:
    """What a procedure carries beside its content: when it applies, and what to do (never executed here)."""

    preconditions: tuple[Precondition, ...] = ()
    steps: tuple[str, ...] = ()


#: The shape of a procedure stored without one: always applicable, no recorded steps.
NO_SHAPE = ProcedureShape()


def _sequence(value: object, code: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ProcedureShapeError(code)
    return value


def _precondition(raw: object) -> Precondition:
    if not isinstance(raw, Mapping):
        raise ProcedureShapeError("precondition_must_be_an_object")
    op = raw.get("op")
    if op == "scope_within":
        if set(raw) != {"op", "scope"} or not isinstance(raw.get("scope"), str):
            raise ProcedureShapeError("precondition_fields_invalid")
        try:
            scope = parse_scope(raw["scope"]).text
        except ScopeError:
            raise ProcedureShapeError("precondition_scope_invalid") from None
        return Precondition("scope_within", scope=scope)
    if op in ("exists", "absent"):
        selector = raw.get("match")
        if set(raw) != {"op", "match"} or not isinstance(selector, Mapping) or not 1 <= len(selector) <= 4:
            raise ProcedureShapeError("precondition_fields_invalid")
        pairs: list[tuple[MatchField, str]] = []
        for field, value in selector.items():
            if field not in MATCH_FIELDS or not isinstance(value, str):
                raise ProcedureShapeError("precondition_match_invalid")
            allowed = _VOCABULARY.get(field)
            if (allowed is not None and value not in allowed) or (
                field == "predicate" and _PREDICATE.match(value) is None
            ):
                raise ProcedureShapeError("precondition_match_invalid")
            pairs.append((cast(MatchField, field), value))
        return Precondition(cast(Operator, op), match=tuple(sorted(pairs)))
    raise ProcedureShapeError("precondition_operator_unknown")


def parse_shape(preconditions: object = None, steps: object = None) -> ProcedureShape:
    """Validate a caller's ``preconditions`` and ``steps`` (each optional) into a :class:`ProcedureShape`.

    ``None`` for either means "not given": no preconditions (always applicable) and no steps.
    """
    parsed: tuple[Precondition, ...] = ()
    if preconditions is not None:
        items = _sequence(preconditions, "preconditions_must_be_a_list")
        if len(items) > MAX_PRECONDITIONS:
            raise ProcedureShapeError("too_many_preconditions")
        parsed = tuple(_precondition(item) for item in items)
    texts: tuple[str, ...] = ()
    if steps is not None:
        items = _sequence(steps, "steps_must_be_a_list")
        if len(items) > MAX_STEPS:
            raise ProcedureShapeError("too_many_steps")
        if not all(isinstance(step, str) and step.strip() and len(step) <= MAX_STEP_LENGTH for step in items):
            raise ProcedureShapeError("step_invalid")
        texts = tuple(cast(str, step) for step in items)
    return ProcedureShape(parsed, texts)


class StoredShapeError(RuntimeError):
    """A stored shape the parser cannot read: the two grammars disagree, which is a defect, not a caller's mistake.

    Deliberately not a ``ValueError``: the entry point would report that as the reader's ``validation_error``. As a
    ``RuntimeError`` it is the content-free ``internal_error`` (and logged), which is what it is.
    """


def _from_row(preconditions: object, steps: object) -> ProcedureShape:
    try:
        return parse_shape(preconditions, steps)
    except ProcedureShapeError as error:
        raise StoredShapeError(error.code) from None


async def record_shape(
    connection: AsyncConnection,
    *,
    user_id: str,
    version_id: str,
    shape: ProcedureShape,
    schema_name: str = "cme_engine",
) -> None:
    """Store ``shape`` for one procedure, in the caller's open transaction. A shape is written once, never updated.

    The JSON is content and goes only to ``procedure_shape``, never to an audit row (``RL-072``).
    """
    preconditions_json = [condition.to_json() for condition in shape.preconditions]
    steps_json = list(shape.steps)
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL("INSERT INTO {t} (version_id, user_id, preconditions, steps) VALUES (%s, %s, %s, %s)").format(
                t=sql.Identifier(schema_name, "procedure_shape")
            ),
            (version_id, user_id, Jsonb(preconditions_json), Jsonb(steps_json)),
        )


async def shapes_for(
    connection: AsyncConnection, *, user_id: str, procedure_ids: Sequence[str], schema_name: str = "cme_engine"
) -> dict[str, ProcedureShape]:
    """The stored shapes of ``procedure_ids`` in one statement. A procedure without a row has no entry (no shape)."""
    del user_id
    if not procedure_ids:
        return {}
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT version_id, preconditions, steps FROM {t} WHERE version_id = ANY(%s)").format(
                t=sql.Identifier(schema_name, "procedure_shape")
            ),
            (list(procedure_ids),),
        )
        return {str(row[0]): _from_row(row[1], row[2]) for row in await cursor.fetchall()}


__all__ = [
    "MATCH_FIELDS",
    "MAX_PRECONDITIONS",
    "MAX_STEPS",
    "MAX_STEP_LENGTH",
    "NO_SHAPE",
    "OPERATORS",
    "MatchField",
    "Operator",
    "Precondition",
    "ProcedureShape",
    "ProcedureShapeError",
    "StoredShapeError",
    "parse_shape",
    "record_shape",
    "shapes_for",
]
