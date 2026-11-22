"""Wire one :class:`~custom_memory_engine.api.entry.Engine` the way a deployment wires it (``E-039``, ``E-011``).

The entry point takes injected collaborators so tests can drive every branch. That also means a deployment could wire
it *incompletely* - most importantly without a ``ScopeReadRecorder``, which would silently disable the cross-scope
audit of ``ARCHITECTURE_V2.md`` section 5 rule 6. This function is the one place that builds a complete engine, and
:func:`tests/test_api_wiring.py` asserts it wires every collaborator a read path needs.

Handlers are registered by their owning tasks (``api.handlers``, ``api.ingest_handlers``); an operation whose task has
not landed stays unwired and answers ``forbidden``, which is the fail-closed behaviour ``E-039`` specifies.
"""

from custom_memory_engine.api.entry import Engine, Handler
from custom_memory_engine.api.runtime import DbMethods, DbTargets, ForeignKeyPrincipals, Runtime
from custom_memory_engine.profile import Profile
from custom_memory_engine.scope.audit import ConnectionScopeReads


def build_engine(runtime: Runtime, *, handlers: dict[str, Handler] | None = None) -> Engine:
    """A fully wired engine: caller-scoped resolver, method registry, principal check and the scope read recorder."""
    return Engine(
        resolver=DbTargets(runtime),
        methods=DbMethods(runtime),
        principals=ForeignKeyPrincipals(),
        handlers=handlers,
        scope_reads=ConnectionScopeReads(runtime.session, schema_name=runtime.schema_name),
    )


__all__ = ["build_engine"]


# --- the complete deployment wiring ---------------------------------------------------------------------

#: Operations the contract declares that no handler owns yet, each with the reason it is not wired. `wire_engine`
#: registers everything else, and `tests/test_api_wiring.py` fails if this list and reality disagree, so an operation
#: cannot quietly stay unwired after its task lands.
UNWIRED_OPERATIONS: dict[str, str] = {
    "verify": "the `verification` wire shape is unspecified (API_V2.md 3 gives one opaque argument), while the "
    "schema's row requires held-out split ids, disjoint temporal windows and context keys (I11)",
    "dissent": "needs a `negative_constraint` reader in inference (I16); a constraint nothing consults would be the "
    "write-only column this project keeps finding",
    "expire": "A-014 (abstraction expiry); V2-D14 is still open on the retention values",
    "rebuild_index": "A-014; returns a job id and needs the queue's index-maintenance handler",
    "export": "E-038 (epoch-bound export snapshots); ExportSnapshotStore exists, the handler does not",
    "stats": "E-036's cost report covers the read path; per-artifact statistics need an outcome-stats reader",
}


def wire_engine(
    runtime: Runtime,
    *,
    profile: Profile | None = None,
    tau_confidence: float = 0.6,
    tau_coverage: float = 0.5,
) -> Engine:
    """Build the engine and register **every** handler that exists (``E-046``).

    Before this function a deployment had to call six ``register_*`` functions by hand, in the right order, with the
    right profile — and forgetting one produced a live service where an operation answers ``forbidden``, which reads
    exactly like a permission bug. Wiring is now one call, and :data:`UNWIRED_OPERATIONS` is the explicit, testable
    statement of what is missing.
    """
    from custom_memory_engine.api.delete_handlers import register_delete_handlers
    from custom_memory_engine.api.handlers import Handlers, register_structure_handlers
    from custom_memory_engine.api.infer_handlers import register_infer_handlers
    from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
    from custom_memory_engine.api.lifecycle_handlers import register_lifecycle_handlers
    from custom_memory_engine.api.maintain_handlers import register_maintain_handlers
    from custom_memory_engine.api.outcome_handlers import register_outcome_handlers
    from custom_memory_engine.api.read_handlers import register_read_handlers

    engine = build_engine(runtime)
    structure: Handlers = register_structure_handlers(engine, runtime)
    register_ingest_handlers(engine, runtime, profile)
    register_read_handlers(engine, runtime)
    register_infer_handlers(engine, runtime, tau_confidence=tau_confidence, tau_coverage=tau_coverage, profile=profile)
    register_delete_handlers(engine, runtime)
    register_maintain_handlers(engine, runtime)
    register_outcome_handlers(engine, runtime)
    register_lifecycle_handlers(engine, structure)
    return engine


__all__ = ["UNWIRED_OPERATIONS", "build_engine", "wire_engine"]
