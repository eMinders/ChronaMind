"""Minimal episodic → semantic/summary consolidation (RL-061, Proposal §7.2, Invariant 10 (C)).

A consolidated version is *derived*, never extracted. It carries no turn evidence;
`MEMORY_DERIVATION` links are what satisfy `Invariant 1`, and a valid range that
covers every source is what makes branch `(C)` archival legal for those sources.

Three deliberate boundaries:

* **No model calls here.** Summarizing text is the caller's job. This package takes
  the summary content as an argument, so consolidation is replayable and testable
  without a provider (mirrors ``engine/ingest.py``).
* **The caller owns the transaction and the tenant.** Every function requires a
  matching ``app.user_id`` and never commits, closes, or changes ``search_path``.
* **Consolidation writes; archival is a separate, explicit call.** Deriving a
  covering replacement does not archive anything. ``archive_version`` remains the
  only door to ``Archived``, so a caller cannot lose a belief by consolidating.

Order matters for correctness: a source row may only be archived *after* its
covering replacement is committed, because ``archive_version`` evaluates branch
``(C)`` against the database.
"""

from custom_memory_engine.consolidation.consolidator import (
    MAX_DERIVATION_DEPTH,
    ConsolidationError,
    ConsolidationOutcome,
    ConsolidationPlan,
    SourceVersion,
    consolidate,
    load_sources,
    plan_consolidation,
)

__all__ = (
    "ConsolidationError",
    "ConsolidationOutcome",
    "ConsolidationPlan",
    "MAX_DERIVATION_DEPTH",
    "SourceVersion",
    "consolidate",
    "load_sources",
    "plan_consolidation",
)
