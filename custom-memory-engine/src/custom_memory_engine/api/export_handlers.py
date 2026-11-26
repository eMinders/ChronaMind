"""The ``export`` handler: the caller's read set as a durable manifest (``API_V2.md`` §4.9, ``V2-D99``).

Same contract as the other handlers: reached only through :meth:`~custom_memory_engine.api.entry.Engine.call`, run as
the
authenticated caller under the restricted role, static errors only. ``API_V2.md`` gives this operation one error of its
own — ``resource_limit`` — which is what :func:`engine.export.export_snapshot` raises when a read set is too large to
name in one manifest, rather than answering with a silent prefix.

**The read set is the request, not the permission.** ``selectors`` is a list of scope paths; each is re-anchored inside
the caller's resolved read set by the same intersection every other read uses
(:func:`~custom_memory_engine.scope.access.read_predicate_named`), so naming a stranger's scope exports nothing from
it.
``max(entry)` is deliberately *not* done here: an empty intersection is a legal, empty manifest ("you can read nothing
there"), which is a truthful answer and not an error — the same choice the empty-read-set path of ``query`` makes.

**Tier C only.** Export is a bulk read, and ``TB9`` keeps bulk reads out of Tier B entirely; a Tier B credential never
reaches this handler (the entry point answers ``forbidden``). That is why the manifest's scope paths are written from
the
*re-anchored* read set rather than from the raw request: the stored record must describe what was actually exported.
"""

from __future__ import annotations

from collections.abc import Mapping

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.engine.export import (
    ExportError,
    ExportManifest,
    export_snapshot,
    selectors,
)
from custom_memory_engine.scope.access import ReadEntry, ReadSet


class ExportHandlers:
    """The ``export`` operation bound to one :class:`Runtime`."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def export(self, call: Call) -> ExportManifest:
        """Capture (or replay) the manifest of everything this caller may read inside ``selectors``."""
        try:
            requested = selectors(call.arguments.get("selectors"))
        except ExportError as exc:
            raise EntryError("validation_error", exc.code) from None
        user = call.ctx.principal
        read_set = self._narrow(call.ctx.read_set, requested)
        async with self.runtime.session(user) as connection:
            try:
                return await export_snapshot(
                    connection,
                    user_id=user,
                    read_set=read_set,
                    selected=requested,
                    schema_name=self.runtime.schema_name,
                )
            except ExportError as exc:
                code = "resource_limit" if exc.code == "export_exceeds_artifact_limit" else "validation_error"
                raise EntryError(code, exc.code) from exc

    @staticmethod
    def _narrow(read_set: ReadSet, requested: tuple[str, ...]) -> ReadSet:
        """The caller's own entries that cover the requested paths, re-anchored at each request.

        The caller's read set was already resolved from the namespace and the active grants (the host does it before
        the call, and ``Engine.call`` carries it). This keeps only the entries that cover the request, so a selector
        narrows and never widens; re-resolving from the grant table here would double-apply that lookup and could
        disagree with the read set the rest of the call is using.
        """
        entries = tuple(
            # anchored at the *requested* path, never at the grant's: keeping the grant's path would read the whole
            # grant while the manifest recorded the narrower selector, so the snapshot would hold rows its own stated
            # scope excludes (`S-Low`, review of #259)
            ReadEntry(request, entry.visibilities, entry.mode)
            for request in requested
            for entry in read_set.entries
            if request == entry.path or (entry.mode == "subtree" and request.startswith(entry.path))
        )
        # An empty result is a real answer: the caller can read nothing inside those paths. `read_predicate_named`
        # turns an empty read set into `false`, so the manifest is empty rather than a prefix of someone else's.
        return ReadSet(entries, ())


def register_export_handlers(engine: Engine, runtime: Runtime) -> ExportHandlers:
    """Wire ``export`` onto ``engine``. The entry point's contract owns the tier and the condition codes."""
    handlers = ExportHandlers(runtime)
    table: Mapping[str, Handler] = {"export": handlers.export}
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["ExportHandlers", "register_export_handlers"]
