"""Association types and the per-artifact degree cap (V2-D13, resolves B-08).

The cap is enforced by the database function ``enforce_association_cap()`` (migration 3);
this module is the only sanctioned way to attach it, so a profile's declared value is
validated before it reaches DDL. See ``003_association_cap.sql`` for the semantics.
"""

from __future__ import annotations

from typing import Literal, get_args

from psycopg import sql

type AssociationType = Literal[
    "related_to", "contradicts", "refines", "specialises", "instance_of",
    "caused_by", "precedes", "part_of", "corroborated_by", "supersedes",
]  # fmt: skip
ASSOCIATION_TYPES: tuple[AssociationType, ...] = get_args(AssociationType.__value__)

DEFAULT_ASSOCIATION_CAP = 32  # V2-D13: the plan's recorded default; profile-overridable
MIN_ASSOCIATION_CAP = 1
MAX_ASSOCIATION_CAP = 1024  # mirrors the trigger's range check


class AssociationPolicyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_cap(cap: object) -> int:
    """Return a profile-declared cap, or raise. Booleans and non-integers are rejected."""
    if isinstance(cap, bool) or not isinstance(cap, int):
        raise AssociationPolicyError("association_cap_must_be_an_integer")
    if not MIN_ASSOCIATION_CAP <= cap <= MAX_ASSOCIATION_CAP:
        raise AssociationPolicyError("association_cap_out_of_range")
    return cap


def attach_cap_trigger(schema_name: str, table_name: str, cap: int = DEFAULT_ASSOCIATION_CAP) -> sql.Composed:
    """DDL attaching the cap to an association table (``from_id``, ``to_id``, ``id`` columns)."""
    validated = validate_cap(cap)
    return sql.SQL(
        "CREATE TRIGGER trg_association_cap BEFORE INSERT OR UPDATE OF from_id, to_id ON {} "
        "FOR EACH ROW EXECUTE FUNCTION {}({})"
    ).format(
        sql.Identifier(schema_name, table_name),
        sql.Identifier(schema_name, "enforce_association_cap"),
        sql.Literal(str(validated)),
    )


def degree_allows(current_degree: int, cap: int = DEFAULT_ASSOCIATION_CAP) -> bool:
    """Reference semantics of the trigger: one more edge is allowed iff degree < cap."""
    return current_degree < validate_cap(cap)


__all__ = [
    "ASSOCIATION_TYPES",
    "DEFAULT_ASSOCIATION_CAP",
    "MAX_ASSOCIATION_CAP",
    "MIN_ASSOCIATION_CAP",
    "AssociationPolicyError",
    "AssociationType",
    "attach_cap_trigger",
    "degree_allows",
    "validate_cap",
]
