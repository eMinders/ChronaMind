"""Content-free audit details and log events (RL-072, Invariant 8, deletion Channel 7).

Audit rows outlive deletion by design, so anything written into ``audit_event.details`` must be safe to keep
after the memory it describes is gone: no content, evidence spans, embeddings, hashes, subjects, predicates or
memory keys (``TECHNICAL_SPECIFICATION.md`` section 7.2 step 5). The database ``CHECK``
``audit_details_content_free`` only rejects three top-level keys, so it is a backstop, not the guarantee.

This module is the guarantee, by allowlist rather than blacklist:

* :func:`content_free_details` accepts only keys in :data:`AUDIT_DETAIL_KEYS`, and only values that are
  ``None``, booleans, integers, identifier- or code-shaped strings (no whitespace, bounded length), or lists of
  such strings. Prose cannot pass: it contains whitespace. A new detail key needs a reviewed edit here.
* :func:`redact_log_event` is a structlog processor that replaces the value of any content-bearing key.

Every engine writer of ``audit_event`` calls :func:`content_free_details` before the ``INSERT``.
"""

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

from psycopg.errors import InvalidParameterValue

# Every key an engine audit writer emits. Adding one is a reviewed decision (RL-072).
AUDIT_DETAIL_KEYS = frozenset(
    {
        # versions and supersession
        "ablation",
        "version_id",
        "candidate_version_id",
        "existing_version_id",
        "inserted_version_id",
        "reissued_version_id",
        "reissued_prior_version_id",
        "reissued_later_version_id",
        "closed_version_id",
        "closed_version_ids",
        "deleted_version_id",
        "deleted_version_ids",
        "truncated_version_id",
        "conflict_id",
        "archival_branch",
        # deletion receipts (counts only)
        "scope",
        "deletion_epoch",
        "deleted_versions",
        "dependents",
        "re_derived_versions",
        "purged_jobs",
        # how many outside-the-boundary sources (D-ENG-04) a deletion listed; a count, never a locator or hash
        "external_exclusions",  # a count of external sources outside the deletion boundary (E-013)
        # soundness assessments (E-015): counts, registered-method identifiers, thresholds as decimal tokens
        "policy",
        "source_count",
        "method_id",
        "method_version",
        "params_hash",
        "support",
        "counter",
        "undetermined",
        "opportunity",
        "coverage",
        "confidence_lower",
        "tau_confidence",
        "tau_coverage",
        "creation_allowed",
        "creation_reasons",
        "admissible_by_bound",
        "verification_reasons",
        # cross-scope reads (E-011): counts only, never a path or a principal
        "selector_count",
        "cross_scope_count",
        # queue and clock
        "job_id",
        "kind",
        "epoch_start",
        "current_epoch",
        "clock_mode",
        "transaction_id",
    }
)
# Keys that carry, or are derived from, memory content. Never logged, never audited.
CONTENT_KEYS = frozenset(
    {
        "content",
        "text",
        "evidence_span",
        "embedding",
        "memory_key",
        "subject_key",
        "predicate_key",
        "target_ref",
        "query",
        "prompt",
        "answer",
        "context",
        "canary",
        "content_hash",
    }
)
MAX_TOKEN_LENGTH = 200
REDACTED = "[redacted]"
_TOKEN = re.compile(r"[A-Za-z0-9_.:@/+-]+")


class AuditContentError(InvalidParameterValue):
    """Static error (SQLSTATE 22023) naming the rule broken, never the offending value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _token(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_TOKEN_LENGTH and _TOKEN.fullmatch(value) is not None


def _allowed(value: object) -> bool:
    if value is None or isinstance(value, bool | int):
        return True
    if isinstance(value, list | tuple):
        return all(_token(item) for item in value)
    return _token(value)


def content_free_details(details: Mapping[str, object]) -> dict[str, object]:
    """Return ``details`` as a plain dict if it is content-free, else raise ``AuditContentError``."""
    if not isinstance(details, Mapping):
        raise AuditContentError("audit_details_must_be_a_mapping")
    checked: dict[str, object] = {}
    for key, value in details.items():
        if key not in AUDIT_DETAIL_KEYS:
            raise AuditContentError("audit_detail_key_not_allowed")
        if not _allowed(value):
            raise AuditContentError("audit_detail_value_not_identifier_shaped")
        checked[key] = list(value) if isinstance(value, tuple) else value
    return checked


def redact_log_event(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """structlog processor: replace the value of every content-bearing key with ``[redacted]``."""
    for key in CONTENT_KEYS.intersection(event_dict):
        event_dict[key] = REDACTED
    return event_dict


__all__ = [
    "AUDIT_DETAIL_KEYS",
    "CONTENT_KEYS",
    "AuditContentError",
    "content_free_details",
    "redact_log_event",
]
