"""The public API entry point (E-039; ``API_V2.md``, ``V2-D46``, ``V2-D47``, ``V2-D52``).

``api.contract`` enumerates the 29 operations as data. This module is the one runtime door to them. Without it each
operation would re-implement authorisation, or none would. For every call it, in this order and before any resolver
or handler runs a statement:

1. builds the caller from an authenticated :class:`Credential` only (tier and ``admin`` are never request arguments);
2. calls :func:`api.contract.authorize`, so an unknown or unpermitted operation is ``forbidden`` and nothing else
   happens;
3. rejects unknown and reserved argument names;
4. enforces **every condition code** the operation declares (:data:`CONDITION_CHECKS`);
5. dispatches to the handler registered for the operation (handlers are wired as their owning tasks land);
6. maps every failure to the static error contract of ``API_V2.md`` section 1.1.

**Errors are static.** :class:`EntryError` carries a ``code`` from the contract and an optional static ``detail``;
its string form is the code only. It never contains an identifier, a path, a scope or content. ``forbidden`` is
the single answer for an absent target and an unreadable one (``THREAT_MODEL.md`` T-11, T-18), and an opaque
provenance handle is answered exactly like an unknown id (``V2-D37``) without a lookup.

``internal_error`` is the seventh code of ``API_V2.md`` section 1.1 (``V2-D58``): the last resort for a failure
that is none of the six above (a bug or an unexpected driver error), reported without its message. It is returned
only by :meth:`Engine.call`, never by an operation's own logic, which is why section 3 does not list it per
operation. It is named here so that such a failure is neither swallowed nor allowed to leak.

HTTP transport is out of scope.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

import structlog
from psycopg.errors import DeadlockDetected, InsufficientPrivilege, ProgramLimitExceeded, SerializationFailure

from custom_memory_engine.api.contract import (
    BY_NAME,
    OPERATIONS,
    ContractError,
    Operation,
    Tier,
    authorize,
)
from custom_memory_engine.engine.channels import opens_contradiction
from custom_memory_engine.model.association import ASSOCIATION_TYPES
from custom_memory_engine.model.axes import LIFECYCLES, Lifecycle
from custom_memory_engine.scope.access import ReadSet, authorize_write, row_visible
from custom_memory_engine.scope.paths import ScopeError, parse_scope
from custom_memory_engine.scope.provenance import is_opaque_reference

logger = structlog.get_logger(__name__)

INTERNAL_ERROR = "internal_error"
# Names a request must never use to try to set who the caller is. (`grant_access` has a legitimate input called
# `principal` - the grantee - so that name is validated like any other input instead.)
RESERVED_ARGUMENTS = frozenset({"tier", "admin", "user_id", "caller", "ctx", "credential", "read_set"})
#: Argument names that name an existing artifact, association, entity or result (single id each).
SINGLE_TARGETS = (
    "artifact_id",
    "result_id",
    "association_id",
    "from",
    "to",
    "entity_a",
    "entity_b",
    "subject_entity",
    "procedure_id",
)
EVIDENCE_ARGUMENTS = ("evidence", "counter_evidence")

type Origin = Literal["tier_b", "tier_c"]


class EntryError(Exception):
    """A failure with a static ``code`` (and optional static ``detail``). ``str()`` is the code, nothing else."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

    def __repr__(self) -> str:
        return f"EntryError({self.code!r})"


# --- the caller ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Credential:
    """What the transport's authenticator established. The only source of tier and admin role."""

    principal: str
    tier: Tier
    admin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.principal, str) or not self.principal.strip():
            raise EntryError("validation_error", "principal_required")
        if self.tier not in ("B", "C"):
            raise EntryError("validation_error", "tier_invalid")
        if not isinstance(self.admin, bool):
            raise EntryError("validation_error", "admin_flag_invalid")


@dataclass(frozen=True, slots=True)
class CallerContext:
    """The authenticated caller and its resolved access. Built from a :class:`Credential`, never from a request."""

    principal: str
    tier: Tier
    admin: bool
    write_scope: str
    read_set: ReadSet

    @classmethod
    def from_credential(cls, credential: Credential, *, write_scope: str, read_set: ReadSet) -> CallerContext:
        try:
            parse_scope(write_scope)
        except ScopeError:
            raise EntryError("validation_error", "write_scope_invalid") from None
        # `admin` is a role within Tier C: it is ignored for Tier B (API_V2.md section 3).
        return cls(
            credential.principal, credential.tier, credential.admin and credential.tier == "C", write_scope, read_set
        )


# --- collaborators the entry point needs (all injected, so tests drive every branch) --------------------


@dataclass(frozen=True, slots=True)
class TargetInfo:
    """What the entry point needs to know about an existing target to authorise a call on it."""

    scope_path: str
    visibility: str
    lifecycle: Lifecycle | None = None
    lineage_owner: str | None = None  # the principal whose method/assertion produced it
    lineage_tier: Tier | None = None  # and the tier it acted as: the same principal acts as B and as C


class TargetResolver(Protocol):
    async def resolve(self, ctx: CallerContext, ids: Sequence[str]) -> Mapping[str, TargetInfo]:
        """Return info for the ids ``ctx`` can read. Absent and unreadable ids are both simply missing.

        The caller is passed so the lookup runs under that caller's row-level security: whether an id is absent or
        merely unreadable is then not even known to the engine, let alone distinguishable (T-11, T-18).
        """
        ...


class MethodRegistry(Protocol):
    async def is_registered(self, ctx: CallerContext, method: Mapping[str, object]) -> bool: ...


class PrincipalRegistry(Protocol):
    """Whether a grantee handle names a real user (``THREAT_MODEL.md`` T-30).

    ``grant_access`` takes a handle obtained out of band. Without this collaborator an unknown handle and a known one
    would be distinguishable (one is ``forbidden``, the other succeeds), which tells a namespace owner whether a user
    id exists.
    """

    async def exists(self, principal: str) -> bool: ...


class ScopeReadRecorder(Protocol):
    """Records a read that reached outside the caller's namespace (``ARCHITECTURE_V2.md`` section 5 rule 6).

    It receives the principal and the scope paths the call actually resolved outside that namespace. It never
    receives content: what it may write is ``scope.audit.record_scope_read``'s decision, and its ``scope_read``
    detail carries counts only.
    """

    async def record(self, principal: str, cross_scope_paths: Sequence[str]) -> None: ...


@dataclass(frozen=True, slots=True)
class Call:
    """What a handler receives: an already authorised, already validated call."""

    ctx: CallerContext
    operation: Operation
    arguments: Mapping[str, object]
    contradiction_origin: Origin | None = None
    opens_contradiction: bool = False
    targets: Mapping[str, TargetInfo] = field(default_factory=dict)


type Handler = Callable[[Call], Awaitable[object]]
type Check = Callable[["Engine", CallerContext, Operation, dict[str, object], dict[str, object]], Awaitable[None]]


def _within_callers_namespace(principal: str, scope_path: str) -> bool:
    """Whether ``scope_path`` lies in the caller's own ``/u/<principal>/`` namespace.

    A malformed principal or path is treated as *not* ours, so it is recorded rather than silently skipped: the
    audit must over-report a doubtful read, never under-report one.
    """
    try:
        return parse_scope(scope_path).is_within(parse_scope("/u/" + principal + "/"))
    except ScopeError:
        return False


# --- condition checks ----------------------------------------------------------------------------------


def _fail(operation: Operation, code: str, detail: str | None = None) -> EntryError:
    """Build an error whose code is allowed for ``operation``; otherwise it degrades to ``validation_error``."""
    return EntryError(code if code in operation.errors else "validation_error", detail)


def _evidence_argument(operation: Operation) -> str | None:
    return next((name for name in EVIDENCE_ARGUMENTS if name in operation.inputs), None)


async def _evidence_required(
    _engine: Engine, _ctx: CallerContext, op: Operation, args: dict[str, object], _state: dict[str, object]
) -> None:
    name = _evidence_argument(op)
    value = args.get(name) if name else None
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or len(value) == 0:
        raise _fail(op, "validation_error", "evidence_required")


def _scope_argument(ctx: CallerContext, op: Operation, args: dict[str, object]) -> object:
    if op.name == "delete":  # `scope` there is memory|topic|all, not a path; the deletion acts in the caller's scope
        return ctx.write_scope
    for name in ("scope", "parent"):
        if name in op.inputs:
            return args.get(name)
    return ctx.write_scope


async def _scope_writable(
    _engine: Engine, ctx: CallerContext, op: Operation, args: dict[str, object], _state: dict[str, object]
) -> None:
    target = _scope_argument(ctx, op, args)
    if target is None:
        raise _fail(op, "validation_error", "scope_required")
    visibility = args.get("visibility", "private")
    try:
        authorize_write(ctx.principal, target, visibility)
    except ScopeError:
        raise _fail(op, "forbidden") from None


async def _namespace_owner_only(
    engine: Engine, ctx: CallerContext, op: Operation, args: dict[str, object], state: dict[str, object]
) -> None:
    target = args.get("scope", args.get("parent"))
    if target is None and "grant_id" in args:
        info = (await engine.resolve_targets(ctx, [str(args["grant_id"])], state)).get(str(args["grant_id"]))
        target = info.scope_path if info is not None else None
        if info is None:
            raise _fail(op, "forbidden")
    if target is None:
        raise _fail(op, "validation_error", "scope_required")
    # A malformed target is a grammar fault, not an authorisation decision: `API_V2.md` 4.10 separates
    # `validation_error` (the grammar of section 6) from `forbidden` (outside the caller's namespace). Answering
    # `forbidden` for an unparseable path would tell a caller its input was well formed.
    if isinstance(target, str):
        try:
            parse_scope(target)
        except ScopeError:
            raise _fail(op, "validation_error", "scope_invalid") from None
    try:
        authorize_write(ctx.principal, target, "private")
    except ScopeError:
        raise _fail(op, "forbidden") from None


async def _registered_method(
    engine: Engine, ctx: CallerContext, op: Operation, args: dict[str, object], _state: dict[str, object]
) -> None:
    method = args.get("method")
    if not isinstance(method, Mapping):
        raise _fail(op, "validation_error", "method_required")
    if not await engine.methods.is_registered(ctx, method):
        raise _fail(op, "rejected", "method_not_registered")


def _target_ids(op: Operation, args: dict[str, object]) -> list[str]:
    ids: list[str] = []
    for name in SINGLE_TARGETS:
        if name in op.inputs:
            if name == "subject_entity" and name not in args:
                continue
            value = args.get(name)
            if not isinstance(value, str) or not value:
                raise _fail(op, "validation_error", "target_required")
            ids.append(value)
    if "sources" in op.inputs:
        sources = args.get("sources")
        if not isinstance(sources, Sequence) or isinstance(sources, str | bytes):
            raise _fail(op, "validation_error", "sources_required")
        if not all(isinstance(s, str) and s for s in sources):
            raise _fail(op, "validation_error", "target_required")
        ids.extend(sources)
    return ids


async def _targets_readable(
    engine: Engine, ctx: CallerContext, op: Operation, args: dict[str, object], state: dict[str, object]
) -> None:
    ids = _target_ids(op, args)
    if not ids:
        return
    # An opaque provenance handle is never an identifier: answer exactly like an unknown id, with no lookup.
    if any(is_opaque_reference(i) for i in ids):
        raise _fail(op, "forbidden")
    found = await engine.resolve_targets(ctx, ids, state)
    for target in ids:
        info = found.get(target)
        if info is None or not row_visible(ctx.read_set, info.scope_path, info.visibility):
            raise _fail(op, "forbidden")  # absent and unreadable are indistinguishable


async def _own_lineage_only(
    engine: Engine, ctx: CallerContext, op: Operation, args: dict[str, object], state: dict[str, object]
) -> None:
    ids = _target_ids(op, args)
    found = await engine.resolve_targets(ctx, ids, state)
    for target in ids:
        info = found.get(target)
        # the same principal acts as Tier B and as Tier C, and each may touch only what it produced in that role
        if info is None or info.lineage_owner != ctx.principal or info.lineage_tier != ctx.tier:
            raise _fail(op, "forbidden")


async def _grounded_endpoint_protected(
    engine: Engine, ctx: CallerContext, op: Operation, args: dict[str, object], state: dict[str, object]
) -> None:
    """A Tier B `contradicts` edge is stored with no channel effect (V2-D48 C-P4); it can never demote a fact."""
    edge_type = args.get("type")
    if edge_type not in ASSOCIATION_TYPES:
        raise _fail(op, "validation_error", "association_type_invalid")
    origin: Origin = "tier_b" if ctx.tier == "B" else "tier_c"
    state["contradiction_origin"] = origin
    if edge_type != "contradicts":
        state["opens_contradiction"] = False
        return
    ids = [str(args["from"]), str(args["to"])]
    found = await engine.resolve_targets(ctx, ids, state)
    lifecycles = tuple(found[i].lifecycle for i in ids if i in found and found[i].lifecycle in LIFECYCLES)
    state["opens_contradiction"] = len(lifecycles) == 2 and opens_contradiction(origin, lifecycles)  # type: ignore[arg-type]


async def _grant_principal(
    engine: Engine, _ctx: CallerContext, op: Operation, args: dict[str, object], _state: dict[str, object]
) -> None:
    """T-30: an unknown grant principal is refused exactly like any other refusal for this operation."""
    principal = args.get("principal")
    if not isinstance(principal, str) or not principal.strip():
        raise _fail(op, "validation_error", "principal_required")
    if not await engine.principal_exists(principal):
        raise _fail(op, "forbidden")


#: Every condition code of ``api.contract.Condition`` has exactly one enforcing check here.
CONDITION_CHECKS: dict[str, Check] = {
    "evidence_required": _evidence_required,
    "scope_writable": _scope_writable,
    "namespace_owner_only": _namespace_owner_only,
    "registered_method": _registered_method,
    "targets_readable": _targets_readable,
    "own_lineage_only": _own_lineage_only,
    "grounded_endpoint_protected": _grounded_endpoint_protected,
}
#: Cheap, argument-only checks first; checks that read the database last.
CONDITION_ORDER = (
    "evidence_required",
    "scope_writable",
    "namespace_owner_only",
    "registered_method",
    "targets_readable",
    "own_lineage_only",
    "grounded_endpoint_protected",
)


# --- the engine ----------------------------------------------------------------------------------------


class Engine:
    """The single callable surface. ``await engine.call(ctx, "assert", {...})``."""

    def __init__(
        self,
        *,
        resolver: TargetResolver,
        methods: MethodRegistry,
        principals: PrincipalRegistry | None = None,
        handlers: Mapping[str, Handler] | None = None,
        scope_reads: ScopeReadRecorder | None = None,
    ) -> None:
        self.resolver = resolver
        self.methods = methods
        self.principals = principals
        self.scope_reads = scope_reads
        self._handlers: dict[str, Handler] = {}
        for name, handler in (handlers or {}).items():
            self.register(name, handler)

    def register(self, name: str, handler: Handler) -> None:
        """Wire the handler of one operation. Only operations of ``API_V2.md`` can be wired."""
        if name not in BY_NAME:
            raise EntryError("forbidden", "unknown_operation")
        self._handlers[name] = handler

    def unwired_operations(self) -> tuple[str, ...]:
        """Operations in the contract that have no handler yet. Calling one is ``forbidden`` (fail closed)."""
        return tuple(op.name for op in OPERATIONS if op.name not in self._handlers)

    async def resolve_targets(
        self, ctx: CallerContext, ids: Sequence[str], state: dict[str, object]
    ) -> Mapping[str, TargetInfo]:
        """Resolve target ids once per call and cache them in the call's state."""
        cache = state.setdefault("targets", {})
        assert isinstance(cache, dict)
        missing = [i for i in dict.fromkeys(ids) if i not in cache]
        if missing:
            cache.update(await self.resolver.resolve(ctx, missing))
        return {i: cache[i] for i in ids if i in cache}

    async def principal_exists(self, principal: str) -> bool:
        """T-30. With no registry wired nothing can be granted, which is the fail-closed answer."""
        if self.principals is None:
            return False
        return await self.principals.exists(principal)

    async def _record_scope_read(self, ctx: CallerContext, spec: Operation, targets: Mapping[str, TargetInfo]) -> None:
        """``ARCHITECTURE_V2.md`` section 5 rule 6: log a read that reached outside the caller's own namespace.

        The read set is what the call actually resolved, not what it may have asked for: only a target the engine
        returned can have been read. A read confined to the caller's namespace records nothing. With no recorder wired
        (a unit test, or a deployment with no audit sink) this is a no-op, never a silent pass: the *recorder* is what
        decides, and `scope.audit.record_scope_read` is the one that writes.
        """
        if self.scope_reads is None or not targets:
            return
        cross = {
            info.scope_path: info.visibility
            for info in targets.values()
            if not _within_callers_namespace(ctx.principal, info.scope_path)
        }
        if not cross:
            return
        await self.scope_reads.record(ctx.principal, tuple(sorted(cross)))

    async def call(self, ctx: CallerContext, operation: str, arguments: Mapping[str, object] | None = None) -> object:
        # 1. Authorisation first: nothing below runs for an unknown or unpermitted operation.
        try:
            spec = authorize(operation, ctx.tier, admin=ctx.admin)
        except ContractError:
            raise EntryError("forbidden") from None
        args = dict(arguments or {})

        # 2. Argument names: the contract's inputs only, and never an attempt to set the caller.
        if RESERVED_ARGUMENTS & args.keys():
            raise _fail(spec, "validation_error", "reserved_argument")
        if not args.keys() <= set(spec.inputs):
            raise _fail(spec, "validation_error", "unknown_argument")

        # 3. Conditions.
        state: dict[str, object] = {}
        try:
            for condition in CONDITION_ORDER:
                if condition in spec.conditions:
                    await CONDITION_CHECKS[condition](self, ctx, spec, args, state)
            # T-30: naming a grantee must not reveal whether that user exists.
            if "principal" in spec.inputs:
                await _grant_principal(self, ctx, spec, args, state)

            # 4. Dispatch.
            handler = self._handlers.get(spec.name)
            if handler is None:
                raise EntryError("forbidden", "operation_not_wired")
            call = Call(
                ctx,
                spec,
                args,
                state.get("contradiction_origin"),  # type: ignore[arg-type]
                bool(state.get("opens_contradiction", False)),
                dict(state.get("targets", {})),  # type: ignore[call-overload]
            )
            result = await handler(call)
            # 5. Rule 6: a read that left the caller's namespace leaves one content-free `scope_read` row. It runs
            #    after the handler so a refused read records nothing, and a failure to record fails the call closed.
            await self._record_scope_read(ctx, spec, call.targets)
            return result
        except EntryError as error:
            raise self._contract_error(spec, error) from None
        except Exception as error:
            raise self._map(spec, error) from None

    # --- error mapping ---------------------------------------------------------------------------------

    @staticmethod
    def _contract_error(spec: Operation, error: EntryError) -> EntryError:
        if error.code in spec.errors or error.code == INTERNAL_ERROR:
            return EntryError(error.code, error.detail)
        return EntryError("validation_error", error.detail)

    @staticmethod
    def _map(spec: Operation, error: Exception) -> EntryError:
        """Translate any other exception to a static code. The exception's message is never carried."""
        if isinstance(error, InsufficientPrivilege | ContractError):
            return EntryError("forbidden")
        if isinstance(error, SerializationFailure | DeadlockDetected) and "retryable_conflict" in spec.errors:
            return EntryError("retryable_conflict")
        if isinstance(error, ProgramLimitExceeded) and "resource_limit" in spec.errors:
            return EntryError("resource_limit")  # e.g. the association degree cap (V2-D13)
        code = getattr(error, "code", None)
        if isinstance(error, ScopeError) and code == "forbidden":
            return EntryError("forbidden")
        if isinstance(error, ValueError) and isinstance(code, str) and code.replace("_", "").isalnum():
            return EntryError("validation_error", code)  # our own modules' static codes
        # content-free on purpose: the type only, never the message, arguments or traceback (log channel 7)
        logger.error("api_call_failed", operation=spec.name, error_type=type(error).__name__)
        return EntryError(INTERNAL_ERROR)


__all__ = [
    "CONDITION_CHECKS",
    "CONDITION_ORDER",
    "INTERNAL_ERROR",
    "RESERVED_ARGUMENTS",
    "Call",
    "CallerContext",
    "Credential",
    "Engine",
    "EntryError",
    "Handler",
    "MethodRegistry",
    "PrincipalRegistry",
    "ScopeReadRecorder",
    "TargetInfo",
    "TargetResolver",
]
