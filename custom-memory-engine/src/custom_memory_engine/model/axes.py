"""The V2 memory axes, the closed lifecycle table, and the v1 mapping (V2-D38, resolves B-05).

``ARCHITECTURE_V2.md`` §1 separates *belief* (``lifecycle``) from *cost* (``retention``);
§2.3 removes ``archived`` as a lifecycle value; yet §12 ``M7`` kept "``archived`` as a
*retention* value", which the three-value retention enum cannot hold. The decision:

* v1 ``archived`` → ``lifecycle='retired'``, ``retention='durable'``. ``retired`` is
  exactly v1 ``archived``: not believed, excluded from ``facts``/``hypotheses``, still
  visible to ``audit_as_of`` and still inside the deletion closure. Retention is a
  cost policy and is not used to encode belief (``V2-D04``).
* The remaining v1 statuses map totally and deterministically (``map_v1_version``).
  Nothing is deleted or rewritten by the mapping; the v1 ``status`` column stays as
  the read-compatibility source.

``V1_BACKFILL_SQL`` is the same mapping as one SQL expression, for ``E-010``'s ``M1``/
``M7`` backfill; a PostgreSQL test proves it equals :func:`map_v1_version` on every
input combination.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

type Kind = Literal["episode", "claim", "procedure", "abstraction"]
type Lifecycle = Literal["observed", "asserted", "inferred", "contested", "retired"]
type Retention = Literal["working", "session", "durable"]
type V1Status = Literal["candidate", "conflict_pending", "rejected", "active", "archived"]
type V1Layer = Literal["episodic", "semantic", "opinion", "persona"]

KINDS: tuple[Kind, ...] = get_args(Kind.__value__)
LIFECYCLES: tuple[Lifecycle, ...] = get_args(Lifecycle.__value__)
RETENTIONS: tuple[Retention, ...] = get_args(Retention.__value__)
V1_STATUSES: tuple[V1Status, ...] = get_args(V1Status.__value__)
V1_LAYERS: tuple[V1Layer, ...] = get_args(V1Layer.__value__)

# §2.3, closed. ``None`` = creation. ("inferred", "inferred") is the verified=true
# transition. ``inferred → contested`` is stated in §2.3 rule 1 and included here.
TRANSITIONS: frozenset[tuple[Lifecycle | None, Lifecycle]] = frozenset(
    {
        (None, "observed"),
        (None, "asserted"),
        (None, "inferred"),
        (None, "contested"),
        ("contested", "asserted"),
        ("contested", "retired"),
        ("asserted", "retired"),
        ("observed", "retired"),
        ("inferred", "retired"),
        ("inferred", "inferred"),
        ("inferred", "contested"),
    }
)

# Kind-conditioned legality (V2-D48, C-P1). The transition table above is kind-blind; this
# is the set of lifecycles each kind may ever hold. Creation and every transition must land
# inside it, so ``inferred → contested → asserted`` cannot turn an abstraction into a fact
# (I1′, I14): an abstraction is never grounded, an episode is never adjudicated, and a
# procedure is never a record of what a source said.
LEGAL_LIFECYCLE: dict[Kind, frozenset[Lifecycle]] = {
    "episode": frozenset({"observed", "inferred", "contested", "retired"}),
    "claim": frozenset({"observed", "asserted", "inferred", "contested", "retired"}),
    "procedure": frozenset({"asserted", "inferred", "contested", "retired"}),
    "abstraction": frozenset({"inferred", "contested", "retired"}),
}

# §1 retention floors: only episodes and procedures may be working/session.
SHORT_RETENTION_KINDS: frozenset[Kind] = frozenset({"episode", "procedure"})


class AxisError(ValueError):
    """Static code; never echoes content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_allowed_transition(source: Lifecycle | None, target: Lifecycle) -> bool:
    """Return whether §2.3 permits ``source → target``. Anything else is a bug."""
    return (source, target) in TRANSITIONS


def require_transition(source: Lifecycle | None, target: Lifecycle, kind: Kind | None = None) -> None:
    """Raise unless ``source → target`` is in the closed table and, given ``kind``, legal for it.

    ``kind=None`` checks the kind-blind table only (used where the operation is kind-generic,
    e.g. the API contract). Every per-artifact check must pass the kind (``E-016``).
    """
    if source is not None and source not in LIFECYCLES:
        raise AxisError("unknown_lifecycle")
    if target not in LIFECYCLES:
        raise AxisError("unknown_lifecycle")
    if not is_allowed_transition(source, target):
        raise AxisError("lifecycle_transition_not_permitted")
    if kind is not None:
        if kind not in KINDS:
            raise AxisError("unknown_kind")
        if target not in LEGAL_LIFECYCLE[kind] or (source is not None and source not in LEGAL_LIFECYCLE[kind]):
            raise AxisError("lifecycle_not_legal_for_kind")


def validate_axes(kind: object, lifecycle: object, retention: object) -> None:
    """Reject unknown values and retention-floor violations (``archived`` is not a retention)."""
    if kind not in KINDS:
        raise AxisError("unknown_kind")
    if lifecycle not in LIFECYCLES:
        raise AxisError("unknown_lifecycle")
    if retention not in RETENTIONS:
        raise AxisError("unknown_retention")
    if lifecycle not in LEGAL_LIFECYCLE[kind]:
        raise AxisError("lifecycle_not_legal_for_kind")
    if retention != "durable" and kind not in SHORT_RETENTION_KINDS:
        raise AxisError("retention_floor_requires_durable")


@dataclass(frozen=True, slots=True)
class V2Axes:
    kind: Kind
    lifecycle: Lifecycle
    retention: Retention
    reason_code: str


_LAYER_KIND: dict[str, Kind] = {
    "episodic": "episode",
    "semantic": "claim",
    "opinion": "claim",
    "persona": "abstraction",
}


def _believed_lifecycle(layer: V1Layer, has_derivation: bool) -> Lifecycle:
    """M1: the lifecycle an *active* v1 row carries, by layer and derivation presence."""
    if layer == "episodic":
        return "observed"
    if layer == "persona":
        return "inferred"
    return "inferred" if has_derivation else "asserted"


def map_v1_version(status: object, layer: object, has_derivation: object) -> V2Axes:
    """Total, deterministic v1 → V2 mapping for one ``memory_version`` row."""
    if status not in V1_STATUSES:
        raise AxisError("unknown_v1_status")
    if layer not in V1_LAYERS:
        raise AxisError("unknown_v1_layer")
    if not isinstance(has_derivation, bool):
        raise AxisError("has_derivation_must_be_boolean")
    kind = _LAYER_KIND[layer]
    if status == "active":
        return V2Axes(kind, _believed_lifecycle(layer, has_derivation), "durable", "v1_active")
    if status == "archived":
        return V2Axes(kind, "retired", "durable", "v1_archived")
    if status == "conflict_pending":
        return V2Axes(kind, "contested", "durable", "v1_conflict_pending")
    if status == "rejected":
        return V2Axes(kind, "retired", "durable", "v1_rejected")
    return V2Axes(kind, "retired", "durable", "v1_unadjudicated_candidate")


# The same mapping for the M1/M7 backfill. ``status``, ``layer`` and ``has_derivation``
# are the input columns/expressions; the result is a 4-field row (kind, lifecycle,
# retention, reason_code) suitable for ``UPDATE ... FROM (SELECT ...)``.
V1_BACKFILL_SQL = """
ROW(
  CASE layer WHEN 'episodic' THEN 'episode' WHEN 'semantic' THEN 'claim'
             WHEN 'opinion' THEN 'claim' WHEN 'persona' THEN 'abstraction' END,
  CASE status
    WHEN 'active' THEN CASE
      WHEN layer = 'episodic' THEN 'observed'
      WHEN layer = 'persona' THEN 'inferred'
      WHEN has_derivation THEN 'inferred'
      ELSE 'asserted' END
    WHEN 'archived' THEN 'retired'
    WHEN 'conflict_pending' THEN 'contested'
    WHEN 'rejected' THEN 'retired'
    WHEN 'candidate' THEN 'retired' END,
  'durable',
  CASE status
    WHEN 'active' THEN 'v1_active' WHEN 'archived' THEN 'v1_archived'
    WHEN 'conflict_pending' THEN 'v1_conflict_pending' WHEN 'rejected' THEN 'v1_rejected'
    WHEN 'candidate' THEN 'v1_unadjudicated_candidate' END
)
"""


__all__ = [
    "KINDS",
    "LEGAL_LIFECYCLE",
    "LIFECYCLES",
    "RETENTIONS",
    "SHORT_RETENTION_KINDS",
    "TRANSITIONS",
    "V1_BACKFILL_SQL",
    "V1_LAYERS",
    "V1_STATUSES",
    "AxisError",
    "Kind",
    "Lifecycle",
    "Retention",
    "V2Axes",
    "is_allowed_transition",
    "map_v1_version",
    "require_transition",
    "validate_axes",
]
