"""Response and request models of the memory inspector (`app/api/v1/memory.py`).

These models are the **single source** of the web app's API types: `scripts/export_openapi.py` writes them into
`docs/api/openapi.json`, and `web/` generates TypeScript from that file. Change a field here, re-export, and the web build
sees the change; never hand-copy a shape into the frontend.

What they deliberately do **not** carry, because the engine does not return it over its API:

* timestamps on an item (the engine's `Artifact` has none; time is asked for through `/timeline`);
* source text or quote spans (`provenance` returns references only, never content);
* a reference's `scope_path` (it names another user's namespace when a grant is involved; the UI has no use for it).

A recorded conversation turn (`kind == "episode"`) arrives in the engine's `facts` channel. It is moved to
`conversation` here, exactly as `memory_engine._render` labels it for the agent: a verbatim record of what was said, never
a known fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseResponse

#: Every response model below is *serialised* with all of its fields, defaults included, so its JSON schema marks them
#: all required. Without this, a field with a default (`known_facts`, `sources`, …) is optional in `docs/api/openapi.json`
#: and the web interface's generated types would read `known_facts?: MemoryItem[]`, a shape the API never sends.
_RESPONSE_SCHEMA = ConfigDict(json_schema_serialization_defaults_required=True)


class _Out(BaseModel):
    """A nested response shape: every field is always present in the JSON the API sends."""

    model_config = _RESPONSE_SCHEMA


class _OutResponse(BaseResponse):
    """A top-level response: every field (including ``request_id``) is always present."""

    model_config = _RESPONSE_SCHEMA


Channel = Literal["facts", "hypotheses", "procedures", "conflicts", "none"]
Kind = Literal["episode", "claim", "procedure", "abstraction"]
Lifecycle = Literal["observed", "asserted", "inferred", "contested", "retired"]


class SourceRefOut(_Out):
    """One provenance reference. Never content: the engine returns references only."""

    readable: bool = Field(..., description="False for an opaque handle to a source the caller cannot read")
    source_type: str | None = Field(
        default=None, description="e.g. 'external' for a turn the app recorded; None when opaque"
    )
    derivation_type: str = Field(..., description="How the item derives from the source")
    ref: str = Field(..., description="The source id (e.g. 'app://session/turn-…') or the opaque per-caller handle")


class BoundOut(_Out):
    """The statistical bound a verified abstraction always renders with (`CHANNELS.md` R3, invariant `I14`)."""

    support_count: int
    opportunity_count: int
    confidence_lower: float
    coverage: float
    method_id: str
    method_version: str
    verified_on: str
    expires_at: datetime


class ConflictSideOut(_Out):
    """One side of an open contradiction. A conflict shows every side and picks none."""

    id: str
    lifecycle: Lifecycle
    text: str


class MemoryItem(_Out):
    """One engine artifact as the inspector shows it."""

    id: str = Field(..., description="The engine's artifact id (a version id)")
    kind: Kind
    lifecycle: Lifecycle
    channel: Channel
    content: str
    verification: str = Field(..., description="e.g. 'unverified' or 'verified'")
    label: str | None = Field(default=None, description="The hypothesis label, when the engine gives one")
    support_summary: str = Field(default="", description="A bounded abstraction's summary; empty otherwise")
    bound: BoundOut | None = Field(
        default=None, description="Present on a bounded abstraction; never omitted when it exists"
    )
    conflict_sides: list[ConflictSideOut] = Field(default_factory=list)
    sources: list[SourceRefOut] = Field(default_factory=list)


class MemoryItemResponse(_OutResponse, MemoryItem):
    """`GET /memory/{id}`: one item, plus the request id every endpoint response carries."""


class MemoryView(_OutResponse):
    """The caller's memory, by channel, with the verbatim conversation record kept apart from known facts."""

    known_facts: list[MemoryItem] = Field(default_factory=list, description="facts channel, kind != episode")
    hypotheses: list[MemoryItem] = Field(default_factory=list)
    procedures: list[MemoryItem] = Field(default_factory=list)
    conflicts: list[MemoryItem] = Field(default_factory=list)
    conversation: list[MemoryItem] = Field(default_factory=list, description="facts channel, kind == episode")

    def all_items(self) -> list[MemoryItem]:
        """Every item in every section, in section order."""
        return [*self.known_facts, *self.hypotheses, *self.procedures, *self.conflicts, *self.conversation]

    def is_empty(self) -> bool:
        """True when no section holds an item."""
        return not self.all_items()


class SearchIn(BaseModel):
    """`POST /memory/search`."""

    text: str = Field(..., min_length=2, max_length=300)


class ProvenanceEdgeOut(_Out):
    """One edge of an item's lineage, at its distance from the item."""

    depth: int
    source: SourceRefOut


class ProvenanceOut(_OutResponse):
    """`GET /memory/{id}/provenance`: a bounded lineage view."""

    id: str
    depth: int
    depth_limited: bool = Field(..., description="True when readable ancestors remain beyond the requested depth")
    edges: list[ProvenanceEdgeOut] = Field(default_factory=list)


class TimelineView(_OutResponse):
    """`GET /memory/timeline`: what the engine believed at one instant (`audit_as_of`, belief time = target time)."""

    at: datetime
    beliefs: list[MemoryItem] = Field(default_factory=list, description="audit rows, kind != episode")
    conversation: list[MemoryItem] = Field(default_factory=list, description="audit rows, kind == episode")


class ChangeOut(_Out):
    """One belief that differs between two instants. Ids the result does not carry are omitted."""

    outcome: Literal["added", "removed", "changed"]
    previous: list[MemoryItem] = Field(default_factory=list)
    current: list[MemoryItem] = Field(default_factory=list)


class ChangesView(_OutResponse):
    """`GET /memory/changes`: the engine's `change` diff between two instants."""

    start: datetime
    end: datetime
    changes: list[ChangeOut] = Field(default_factory=list)


class AdjudicateIn(BaseModel):
    """`POST /memory/{id}/adjudicate`: only the user turns a held claim into a fact (`TB4`)."""

    decision: Literal["confirm", "deny"]
    note: str | None = Field(default=None, max_length=500)


class AdjudicateResult(_OutResponse):
    """The engine's content-free receipt for an adjudication."""

    id: str
    effect: str = Field(..., description="'asserted' after confirm, 'retired' after deny")
    recorded_at: datetime


class ForgetIn(BaseModel):
    """`POST /memory/forget`: the caller must type the word, so a stray request cannot erase a user."""

    confirm: Literal["DELETE"]


class DeletionCounts(_Out):
    """The engine's deletion receipt, counts only: no request digest, no exclusions list, no content."""

    deletion_epoch: int
    deleted_versions: int
    dependents: int
    rederived_dependents: int = 0
    retired_dependents: int = 0
    deleted_turns: int = 0
    deleted_entities: int = 0
    deleted_mentions: int = 0


class ForgetChecks(_Out):
    """What was observed after the erasure. Each value is measured, never assumed.

    ``None`` means the check could not be run (the engine refused or was unreachable after the erasure committed). The
    erasure itself is already final at that point, so the receipt is still returned; the UI shows the check as not
    verified rather than as passed.

    ``chat_history_cleared`` is false when any of the user's chat sessions could not be cleared. Such a transcript
    survives the erasure and the agent still reads it, so the forget is **not complete**: the caller shows it as failed
    and retries ``POST /forget``, which is idempotent (a second erasure finds nothing and the clearing runs again).
    """

    chat_history_cleared: bool = Field(
        default=False, description="Every chat session of the user was cleared; false means a transcript survived"
    )
    memory_list_empty: bool | None = Field(default=None, description="GET /memory/ returned no item in any section")
    export_artifact_count: int | None = Field(
        default=None, description="artifact_count of an engine export over /u/<id>/"
    )
    old_item_not_found: bool | None = Field(
        default=None,
        description="get() of an id listed before the erasure answered forbidden; None when there was none",
    )


class ForgetResult(_OutResponse):
    """`POST /memory/forget`: the receipt, how many chat sessions were cleared or failed, and the after-checks."""

    receipt: DeletionCounts
    cleared_chat_sessions: int
    failed_chat_sessions: int = Field(
        default=0, description="Sessions whose transcript could not be cleared; any is a forget to retry"
    )
    checks: ForgetChecks


class EngineHealth(_Out):
    """`GET /memory/health`: content-free, unauthenticated."""

    engine: Literal["up", "down", "not_configured"]
