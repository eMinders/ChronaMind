"""Grounding per declared source type (G1a; V2-D41, resolves B-07).

``ARCHITECTURE_V2.md`` §2.2 G1a requires "evidence exists in a **declared source
type**"; migration ``M4`` turns ``memory_evidence.turn_id`` into
``SOURCE(type, ref, span, content_hash)`` and permits ``type='external'``. The v1 gate
only knew user turns. The decision (option 1 of B-07), with every rule fail-closed:

=============  ===========================  =============================================
source type    what it is                   required to ground a span
=============  ===========================  =============================================
``turn``       a *declared* fact            verbatim span in a **user-authored** turn
                                            (identical to v1 ``evidence_supported``)
``external``   *imported* evidence          ref; captured content whose canonical SHA-256
                                            equals the declared ``content_hash``; verbatim
                                            span; ``fetched_at`` aware, not in the future,
                                            and no older than the caller's ``max_age``
``artifact``   an *inferred* conclusion     never — inference is not evidence (§2.3 rule 1:
                                            promotion needs *new grounded* evidence)
anything else  —                            never (``unsupported_source_type``)
=============  ===========================  =============================================

A candidate is grounded only if **every** evidence item grounds. External content is
outside the deletion boundary (``API_V2.md`` §4.1) and is labelled so on its record.
Canonicalisation for ``content_hash``: Unicode NFC, then ``\\r\\n``/``\\r`` → ``\\n``,
UTF-8, SHA-256 hex.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from custom_memory_engine.engine.gate import evidence_supported

type SourceType = Literal["turn", "external"]
DECLARED_SOURCE_TYPES: tuple[SourceType, ...] = ("turn", "external")


def canonical_hash(content: str) -> str:
    """The ``content_hash`` canonicalisation fixed by V2-D41."""
    normalized = unicodedata.normalize("NFC", content).replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Evidence:
    """One evidence item as submitted. ``content`` is the turn text or the captured external text."""

    source_type: str
    ref: str
    span: str
    content: str
    role: str | None = None  # turns only: "user" | "assistant"
    content_hash: str | None = None  # external only: declared at capture time
    fetched_at: datetime | None = None  # external only


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """What M4's ``SOURCE`` row stores for a grounded item."""

    source_type: SourceType
    ref: str
    span: str
    content_hash: str
    observed_at: datetime | None
    in_deletion_boundary: bool


@dataclass(frozen=True, slots=True)
class GroundingResult:
    grounded: bool
    reasons: tuple[str, ...]
    sources: tuple[SourceRecord, ...]


def _check(item: Evidence, now: datetime, max_external_age: timedelta) -> tuple[str | None, SourceRecord | None]:
    if not isinstance(item, Evidence):
        return "evidence_item_invalid", None
    if item.source_type == "artifact":
        return "inferred_artifact_is_not_evidence", None
    if item.source_type not in DECLARED_SOURCE_TYPES:
        return "unsupported_source_type", None
    if not item.ref or not item.span or not item.content:
        return "incomplete_source", None
    if item.source_type == "turn":
        if item.role not in ("user", "assistant"):
            return "incomplete_source", None
        if not evidence_supported((item.span,), ((item.role, item.content),)):
            return "span_not_in_user_turn", None
        return None, SourceRecord("turn", item.ref, item.span, canonical_hash(item.content), None, True)
    # external
    if item.content_hash is None or item.fetched_at is None:
        return "incomplete_source", None
    if item.fetched_at.utcoffset() is None:
        return "fetched_at_must_be_timezone_aware", None
    if canonical_hash(item.content) != item.content_hash:
        return "content_hash_mismatch", None
    if item.span not in item.content:
        return "span_not_in_source_content", None
    if item.fetched_at > now:
        return "fetched_at_in_future", None
    if now - item.fetched_at > max_external_age:
        return "stale_source", None
    record = SourceRecord("external", item.ref, item.span, item.content_hash, item.fetched_at, False)
    return None, record


def ground(evidence: Sequence[Evidence], *, now: datetime, max_external_age: timedelta) -> GroundingResult:
    """Ground a candidate's evidence set; grounded only if non-empty and every item grounds."""
    if now.utcoffset() is None:
        raise ValueError("now_must_be_timezone_aware")
    if max_external_age <= timedelta(0):
        raise ValueError("max_external_age_must_be_positive")
    if not evidence:
        return GroundingResult(False, ("no_evidence",), ())
    reasons: list[str] = []
    records: list[SourceRecord] = []
    for item in evidence:
        reason, record = _check(item, now, max_external_age)
        if reason is not None:
            reasons.append(reason)
        elif record is not None:
            records.append(record)
    if reasons:
        return GroundingResult(False, tuple(dict.fromkeys(reasons)), ())
    return GroundingResult(True, (), tuple(records))


__all__ = [
    "DECLARED_SOURCE_TYPES",
    "Evidence",
    "GroundingResult",
    "SourceRecord",
    "SourceType",
    "canonical_hash",
    "ground",
]
