"""Public ``delete`` handler wired through :meth:`~custom_memory_engine.api.entry.Engine.call` (E-037; ``API_V2.md``).

Tier C only, ``scope_writable`` enforced by the entry point (``api/contract.py``). Every deletion runs inside
:meth:`Runtime.session` as the authenticated caller under the restricted ``NOSUPERUSER NOBYPASSRLS`` role, computes
``deletion_request.target_ref`` with ``runtime.deletion_key`` (``T-29``), and traverses the unbounded cognitive
closure with two-phase ordering (``V2-D09``, ``V2-D50``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.deletion.closure import DeletionPolicyError
from custom_memory_engine.deletion.markers import ErasureMarker
from custom_memory_engine.deletion.rederive import ReDerivationError, SummaryBuilder
from custom_memory_engine.engine.deletion import DeletionError, DeletionReceipt, DeletionScope, delete_memory
from custom_memory_engine.engine.transactions import AblationMode

_VALID_SCOPES: frozenset[str] = frozenset({"memory", "topic", "all"})
_RETRYABLE_CODES: frozenset[str] = frozenset({"closure_growth_retry_exhausted"})


class DeleteHandlers:
    """The ``delete`` operation bound to one :class:`Runtime`."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        summary_builder: SummaryBuilder | None = None,
        rederivation_builder: SummaryBuilder | None = None,
        threshold: int = 1,
        delete_exclusive_sources: bool = True,
        ablation: AblationMode = "P",
    ) -> None:
        self.runtime = runtime
        self.summary_builder = summary_builder
        self.rederivation_builder = rederivation_builder
        self.threshold = threshold
        self.delete_exclusive_sources = delete_exclusive_sources
        self.ablation: AblationMode = ablation

    async def delete(self, call: Call) -> DeletionReceipt:
        """Delete a memory, topic or all memories for the authenticated Tier C caller (``API_V2.md`` §3, §4.8)."""
        scope = call.arguments.get("scope")
        if not isinstance(scope, str) or scope not in _VALID_SCOPES:
            raise EntryError("validation_error", "invalid_deletion_scope")
        target_ref = call.arguments.get("target_ref", "")
        if target_ref is None:
            target_ref = ""
        if not isinstance(target_ref, str):
            raise EntryError("validation_error", "target_ref_invalid")
        if scope != "all" and not target_ref.strip():
            raise EntryError("validation_error", "nonblank_target_ref_required")
        raw_epoch = call.arguments.get("deletion_epoch")
        if raw_epoch is not None and (isinstance(raw_epoch, bool) or not isinstance(raw_epoch, int) or raw_epoch < 1):
            raise EntryError("validation_error", "positive_deletion_epoch_required")

        user = call.ctx.principal
        try:
            async with self.runtime.session(user) as connection:
                return await delete_memory(
                    connection,
                    user,
                    scope=cast(DeletionScope, scope),
                    target_ref=target_ref,
                    ablation=self.ablation,
                    deletion_epoch=raw_epoch,
                    summary_builder=self.summary_builder,
                    rederivation_builder=self.rederivation_builder,
                    threshold=self.threshold,
                    delete_exclusive_sources=self.delete_exclusive_sources,
                    receipt_key=self.runtime.deletion_key,
                    schema_name=self.runtime.schema_name,
                )
        except (DeletionError, DeletionPolicyError, ReDerivationError) as exc:
            # API_V2.md §4.8: closure growth past the bounded lock retries is a conflict the caller may retry.
            if exc.code in _RETRYABLE_CODES:
                raise EntryError("retryable_conflict", exc.code) from None
            raise EntryError("validation_error", exc.code) from None

    async def replay(
        self,
        user: str,
        *,
        scope: DeletionScope,
        target_ref: str,
        request_id: str,
        stored_target_ref: str,
        deletion_epoch: int,
        erasure_markers: Sequence[ErasureMarker] = (),
    ) -> DeletionReceipt:
        """Re-apply one journalled deletion after a restore, with this handler's own configuration (``V2-D122``).

        Not an API operation: only :func:`custom_memory_engine.deletion.journal.replay_journal` calls it. The request
        keeps its original id, digest and epoch, so the restored database ends in the state the journal records.
        Replay records the write markers and erases the journalled turns with their jobs. For ``all``, the scope
        selects every restored turn of the user, including turns absent from a legacy entry (``V2-D125``).
        """
        async with self.runtime.session(user) as connection:
            return await delete_memory(
                connection,
                user,
                scope=scope,
                target_ref=target_ref,
                ablation=self.ablation,
                deletion_epoch=deletion_epoch,
                summary_builder=self.summary_builder,
                rederivation_builder=self.rederivation_builder,
                threshold=self.threshold,
                delete_exclusive_sources=self.delete_exclusive_sources,
                receipt_key=self.runtime.deletion_key,
                schema_name=self.runtime.schema_name,
                request_id=request_id,
                stored_target_ref=stored_target_ref,
                erasure_markers=tuple(erasure_markers),
            )


def register_delete_handlers(
    engine: Engine,
    runtime: Runtime,
    *,
    summary_builder: SummaryBuilder | None = None,
    rederivation_builder: SummaryBuilder | None = None,
    threshold: int = 1,
    delete_exclusive_sources: bool = True,
    ablation: AblationMode = "P",
) -> DeleteHandlers:
    """Wire ``delete`` onto ``engine`` (``E-037``, ``E-039``)."""
    handlers = DeleteHandlers(
        runtime,
        summary_builder=summary_builder,
        rederivation_builder=rederivation_builder,
        threshold=threshold,
        delete_exclusive_sources=delete_exclusive_sources,
        ablation=ablation,
    )
    table: Mapping[str, Handler] = {"delete": handlers.delete}
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["DeleteHandlers", "register_delete_handlers"]
