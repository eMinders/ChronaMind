"""Privacy-preserving provenance references (I17; V2-D37, resolves B-03).

``ARCHITECTURE_V2.md`` §9.3 rendered an unreadable source as "id + scope + type".
``I17`` requires that such a reference expose "no content and no existence oracle",
and §5.2 forbids the equivalent entity oracle. The scope path names the other
tenant and its project structure, and the raw source id is a probe handle, so both
are withheld. The decision (V2-D37):

* **Readable source** (the caller's read set covers its scope and visibility):
  the full reference — source id, scope, source type, derivation type.
* **Unreadable source:** an :class:`OpaqueReference` carrying only
  - ``opaque_ref`` = HMAC-SHA256(key, caller ‖ source id), truncated. It is stable
    for one caller (lineage and de-duplication still work), differs across callers
    (no cross-tenant correlation) and cannot be inverted without the key;
  - ``derivation_type`` — an attribute of the *caller's own* artifact's edge, not of
    the hidden source.

  Source type, scope, id, timestamps and content are not rendered.

An opaque handle is never accepted as an identifier: ``is_opaque_reference`` lets
the API answer a lookup of one exactly as it answers an unknown id. Rendering is
read-only; deletion closure and audit operate on internal ids and are unaffected.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field

from custom_memory_engine.scope.access import ReadSet, row_visible
from custom_memory_engine.scope.paths import ScopeError, parse_scope, user_scope

OPAQUE_PREFIX = "opq_"
OPAQUE_HEX_LENGTH = 32  # 128 bits
MIN_KEY_BYTES = 32
_OPAQUE = re.compile(rf"{OPAQUE_PREFIX}[0-9a-f]{{{OPAQUE_HEX_LENGTH}}}\Z")


class ProvenanceError(ValueError):
    """Static code; never echoes a source id, scope or key."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProvenanceKey:
    """Server-held secret for opaque handles. ``repr`` never shows it."""

    secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.secret, bytes) or len(self.secret) < MIN_KEY_BYTES:
            raise ProvenanceError("provenance_key_too_short")


@dataclass(frozen=True, slots=True)
class ProvenanceSource:
    """Internal description of one source edge of a readable artifact."""

    source_id: str
    scope_path: str
    visibility: str
    source_type: str
    derivation_type: str


@dataclass(frozen=True, slots=True)
class VisibleReference:
    source_id: str
    scope_path: str
    source_type: str
    derivation_type: str
    readable: bool = True


@dataclass(frozen=True, slots=True)
class OpaqueReference:
    opaque_ref: str
    derivation_type: str
    readable: bool = False


type ProvenanceReference = VisibleReference | OpaqueReference


def opaque_handle(key: ProvenanceKey, caller_user_id: str, source_id: str) -> str:
    """Per-caller, non-invertible, stable handle for a hidden source."""
    user_scope(caller_user_id)  # validates the caller identity
    if not isinstance(source_id, str) or not source_id:
        raise ProvenanceError("source_id_required")
    message = caller_user_id.encode() + b"\x00" + source_id.encode()
    digest = hmac.new(key.secret, message, hashlib.sha256).hexdigest()
    return OPAQUE_PREFIX + digest[:OPAQUE_HEX_LENGTH]


def render_reference(
    source: ProvenanceSource, *, caller_user_id: str, read_set: ReadSet, key: ProvenanceKey
) -> ProvenanceReference:
    """Render one provenance edge for ``caller_user_id``, withholding unreadable sources."""
    try:
        parse_scope(source.scope_path)
    except ScopeError:
        # A source whose scope cannot even be parsed is never readable: fail closed.
        return OpaqueReference(opaque_handle(key, caller_user_id, source.source_id), source.derivation_type)
    if row_visible(read_set, source.scope_path, source.visibility):
        return VisibleReference(source.source_id, source.scope_path, source.source_type, source.derivation_type)
    return OpaqueReference(opaque_handle(key, caller_user_id, source.source_id), source.derivation_type)


def render_bundle(
    sources: list[ProvenanceSource], *, caller_user_id: str, read_set: ReadSet, key: ProvenanceKey
) -> tuple[ProvenanceReference, ...]:
    """Render every edge; order is preserved so the bundle stays deterministic."""
    return tuple(
        render_reference(source, caller_user_id=caller_user_id, read_set=read_set, key=key) for source in sources
    )


def is_opaque_reference(value: object) -> bool:
    """True for an opaque handle, which must be treated exactly like an unknown id."""
    return isinstance(value, str) and _OPAQUE.fullmatch(value) is not None


def log_fields(reference: ProvenanceReference) -> dict[str, object]:
    """Content-free structured-log representation (channel 7 / RL-072 rule)."""
    if isinstance(reference, OpaqueReference):
        return {"provenance": "opaque", "derivation_type": reference.derivation_type}
    return {"provenance": "visible", "derivation_type": reference.derivation_type}


__all__ = [
    "OPAQUE_PREFIX",
    "OpaqueReference",
    "ProvenanceError",
    "ProvenanceKey",
    "ProvenanceReference",
    "ProvenanceSource",
    "VisibleReference",
    "is_opaque_reference",
    "log_fields",
    "opaque_handle",
    "render_bundle",
    "render_reference",
]
