"""Semantic understanding: the one layer where a model belongs, and the deterministic checks around it (W3.5).

The review that opened this phase found the same two-sided boundary error in every engine it examined, ChronaMind
included: a model was used where a constraint was needed (free-form reflection written straight into storage) and
absent where it is irreplaceable (reading a natural, paraphrased, multilingual turn). This package is the correction,
and it is deliberately shaped so that each responsibility has exactly one owner:

=============================================  ==========================================================
:mod:`~custom_memory_engine.semantic.contracts`  the boundary: what extraction must return (structured, typed) and
                                                  the protocols a deployment injects -- it imports no provider SDK,
                                                  exactly as the rest of the engine imports none
:mod:`~custom_memory_engine.semantic.grounding`   deterministic: a quoted span must exist in its source, and a claimed
                                                  number/date/identifier must appear in the same source
:mod:`~custom_memory_engine.semantic.authority`   deterministic: `USER > SYSTEM > ASSISTANT > THIRD_PARTY`, derived
                                                  from the evidence's own type and role, never from its text
:mod:`~custom_memory_engine.semantic.slots`       deterministic: one normalization of an open slot key, so a backfill
                                                  and a handler produce the same string
:mod:`~custom_memory_engine.semantic.temporal`     deterministic: an ISO instant is valid, ordered and inside the
                                                  window it claims, whatever language it was written in
:mod:`~custom_memory_engine.semantic.entities`     hybrid: exact/trigram/vector shortlist, adjudication only for the
                                                  borderline rest
:mod:`~custom_memory_engine.semantic.conflict`     hybrid: vector+entity recall, an adjudicated relation, a
                                                  deterministic commit that keeps the GiST invariant
:mod:`~custom_memory_engine.semantic.ingestor`     orchestration: the fast/slow lane that makes one turn durable
                                                  immediately and its structured claims shortly after
===========================================  ============================================================

**Why a package and not a module.** The engine has one door (``api.entry.Engine``) and one write path
(``engine.ingest.apply_candidate``). This package sits *above* both: it turns language into the candidates those
already-verified paths accept, and it never writes a row itself except through them. That keeps every existing
guarantee -- RLS, the temporal exclusion, provenance, channel separation, deletion closure -- exactly where it is,
and means removing the package degrades the engine to what it was before (stored, governed, unread-for-meaning) rather
than leaving a hole in the ledger.

**What is deliberately not here.** No prompt templates, no provider client, no retry policy, no model name. Those
belong to the deployment, and the evaluation harness freezes them so a replay is exact (``RL-032``). The engine's
contribution is the *contract* and the *checks*, because that is what makes an injected model safe to trust.
"""

from custom_memory_engine.semantic.contracts import (
    AdjudicatedRelation,
    CandidateBelief,
    EntityMention,
    ExtractedClaim,
    ExtractionRequest,
    ExtractionResult,
    StructuredExtractor,
    TemporalBounds,
)

__all__ = [
    "AdjudicatedRelation",
    "CandidateBelief",
    "EntityMention",
    "ExtractedClaim",
    "ExtractionRequest",
    "ExtractionResult",
    "StructuredExtractor",
    "TemporalBounds",
]
