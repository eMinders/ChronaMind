"""The memory inspector: the signed-in user's own long-term memory, read and managed through the ChronaMind engine.

Every route acts **as the signed-in user** and nobody else. The engine principal is always ``str(user.id)`` taken from
the user token (``get_current_user``), the same spelling the agent uses (``str(session.user_id)``), so these routes see
exactly the memory the agent wrote. No request field can name another principal or choose a tier: the tier comes from
the inspector's token on the engine host (Tier C, delegating; ``V2-D120``, ``T-42``).

The routes are a thin, typed window onto engine operations; nothing is computed here that the engine did not answer,
and nothing is cached (``V2-D116``):

================================  ==========================================================================
route                             engine call
================================  ==========================================================================
``GET  /health``                  host ``GET /healthz`` (no authentication, content-free)
``GET  /``                        ``query`` mode ``current_state``, no text: everything current, by channel
``POST /search``                  ``query`` mode ``current_state`` with the caller's text
``GET  /timeline?at=``            ``query`` mode ``audit_as_of``, ``target_time = belief_time = at``
``GET  /changes?start=&end=``     ``query`` mode ``change``
``GET  /{id}``                    ``get``
``GET  /{id}/provenance``         ``provenance``
``POST /{id}/adjudicate``         ``adjudicate`` (confirm or deny a ``contested`` claim)
``POST /forget``                  ``delete`` scope ``all``, then every chat session of the user is cleared,
                                  then three after-checks (``query``, ``export``, ``get``)
================================  ==========================================================================

Errors carry the engine's own static code in ``{"detail": {"code", "detail"}}``. ``forbidden`` is answered with 404
because the engine gives the same answer for "absent" and "not yours", and so does this router.

No ``from __future__ import annotations`` here: ``slowapi`` wraps each route, and FastAPI would then resolve string
annotations against the wrapper's module instead of this one.
"""

from datetime import UTC, datetime
from typing import Any, NoReturn

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)

from app.api.v1.auth import get_current_user
from app.api.v1.chatbot import agent
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.user import User
from app.schemas.memory import (
    AdjudicateIn,
    AdjudicateResult,
    ChangesView,
    EngineHealth,
    ForgetChecks,
    ForgetIn,
    ForgetResult,
    MemoryItemResponse,
    MemoryView,
    ProvenanceOut,
    SearchIn,
    TimelineView,
)
from app.services.database import database_service
from app.services.memory_engine import EngineCallError, memory_inspector
from app.services.memory_view import (
    EngineShapeError,
    changes_from_query,
    deletion_counts,
    item_from_artifact,
    provenance_from_view,
    timeline_from_query,
    view_from_query,
)

router = APIRouter()
LIMIT = settings.RATE_LIMIT_ENDPOINTS["memory"][0]

#: The engine's static codes as HTTP statuses. `forbidden` is 404: "absent" and "not yours" are one answer (`T-11`).
STATUS_BY_CODE = {
    "validation_error": 422,
    "forbidden": 404,
    "precondition_failed": 409,
    "resource_limit": 429,
    "retryable_conflict": 409,
    "internal_error": 502,
    "engine_unavailable": 502,
}
#: The engine's bound on a provenance walk (`engine/provenance.py::MAX_PROVENANCE_DEPTH`).
MAX_PROVENANCE_DEPTH = 8
#: Enough for a demo user's whole memory in one page; the engine allows up to 1000.
LIST_LIMIT = 200


def _refuse(code: str, detail: str | None, status: int) -> NoReturn:
    raise HTTPException(status_code=status, detail={"code": code, "detail": detail})


def _configured() -> None:
    if not settings.MEMORY_ENGINE_URL or not settings.MEMORY_ENGINE_USER_TOKEN:
        _refuse("inspector_not_configured", None, 503)


async def _call(user: User, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One engine operation acting as ``user``; refusals propagate as `EngineCallError`."""
    return await memory_inspector.call(str(user.id), operation, arguments)


def _http_error(error: EngineCallError, operation: str) -> NoReturn:
    logger.warning("memory_inspector_call_refused", operation=operation, code=error.code, status=error.status)
    _refuse(error.code, error.detail, STATUS_BY_CODE.get(error.code, 502))


async def engine(user: User, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One engine operation acting as ``user``; a refusal becomes an HTTP error carrying the engine's static code."""
    _configured()
    try:
        return await _call(user, operation, arguments)
    except EngineCallError as error:
        _http_error(error, operation)


def _shape_error(error: EngineShapeError) -> NoReturn:
    logger.error("memory_inspector_unexpected_engine_shape", shape=str(error))
    _refuse("engine_response_invalid", None, 502)


def _own_scope(user: User) -> str:
    return f"/u/{user.id}/"


def _current_state(user: User, limit: int = LIST_LIMIT) -> dict[str, Any]:
    return {"request": {"mode": "current_state", "scope": _own_scope(user), "limit": limit}}


def _past_instant(value: datetime, name: str) -> str:
    """An aware instant that is not in the future, as ISO-8601 UTC. The engine re-checks it against its own clock."""
    if value.tzinfo is None:
        _refuse("validation_error", f"{name}_needs_timezone", 422)
    if value > datetime.now(UTC):
        _refuse("validation_error", f"{name}_in_future", 422)
    return value.astimezone(UTC).isoformat()


# -- static routes first: `/timeline`, `/changes`, `/search`, `/forget` must never be read as an artifact id ----------


@router.get("/health", response_model=EngineHealth)
@limiter.limit(LIMIT)
async def memory_health(request: Request) -> EngineHealth:
    """Whether the memory engine answers. Content-free and unauthenticated, for the interface's status indicator."""
    if not settings.MEMORY_ENGINE_URL or not settings.MEMORY_ENGINE_USER_TOKEN:
        return EngineHealth(engine="not_configured")
    return EngineHealth(engine="up" if await memory_inspector.health() else "down")


@router.get("/", response_model=MemoryView)
@limiter.limit(LIMIT)
async def list_memory(
    request: Request,
    limit: int = Query(LIST_LIMIT, ge=1, le=1000),
    user: User = Depends(get_current_user),
) -> MemoryView:
    """Everything the engine currently holds for the user, by channel; recorded turns in ``conversation``."""
    result = await engine(user, "query", _current_state(user, limit))
    try:
        return view_from_query(result)
    except EngineShapeError as error:
        _shape_error(error)


@router.post("/search", response_model=MemoryView)
@limiter.limit(LIMIT)
async def search_memory(request: Request, search: SearchIn, user: User = Depends(get_current_user)) -> MemoryView:
    """The user's memory ranked against a text (hybrid recall in the engine), by channel."""
    body = {"mode": "current_state", "scope": _own_scope(user), "text": search.text, "limit": 20}
    result = await engine(user, "query", {"request": body})
    try:
        return view_from_query(result)
    except EngineShapeError as error:
        _shape_error(error)


@router.get("/timeline", response_model=TimelineView)
@limiter.limit(LIMIT)
async def memory_timeline(request: Request, at: datetime, user: User = Depends(get_current_user)) -> TimelineView:
    """What the engine believed at instant ``at`` (``audit_as_of``; no text, the mode refuses one: ``V2-D86``)."""
    instant = _past_instant(at, "at")
    body = {
        "mode": "audit_as_of",
        "scope": _own_scope(user),
        "target_time": instant,
        "belief_time": instant,
        "limit": LIST_LIMIT,
    }
    result = await engine(user, "query", {"request": body})
    try:
        return timeline_from_query(at, result)
    except EngineShapeError as error:
        _shape_error(error)


@router.get("/changes", response_model=ChangesView)
@limiter.limit(LIMIT)
async def memory_changes(
    request: Request, start: datetime, end: datetime, user: User = Depends(get_current_user)
) -> ChangesView:
    """What changed between ``start`` and ``end`` (the engine's ``change`` diff, ``V2-D81``)."""
    window_start, window_end = _past_instant(start, "start"), _past_instant(end, "end")
    if start >= end:
        _refuse("validation_error", "window_must_be_nonempty", 422)
    body = {
        "mode": "change",
        "scope": _own_scope(user),
        "window_start": window_start,
        "window_end": window_end,
        "limit": LIST_LIMIT,
    }
    result = await engine(user, "query", {"request": body})
    try:
        return changes_from_query(start, end, result)
    except EngineShapeError as error:
        _shape_error(error)


@router.post("/forget", response_model=ForgetResult)
@limiter.limit(LIMIT)
async def forget_me(request: Request, body: ForgetIn, user: User = Depends(get_current_user)) -> ForgetResult:
    """Erase everything the engine holds for the user, clear their chat histories, and report what was observed.

    Order matters. The engine erasure runs first and is final once it answers; only then are the app's transcripts
    cleared, because the agent re-reads a session's whole history every turn and would otherwise keep answering from
    it. If the erasure fails, nothing is cleared. The after-checks run last and report observations, never assumptions.

    A transcript that cannot be cleared is counted in ``failed_chat_sessions`` and turns ``chat_history_cleared``
    false: the forget is then incomplete, and the caller is told so rather than told "forgotten". Calling this route
    again is safe and retries the clearing (the second erasure finds nothing to delete).
    """
    del body  # its only job is the typed confirmation word, which validation already checked
    before = await engine(user, "query", _current_state(user))
    try:
        probe = next((item.id for item in view_from_query(before).all_items()), None)
    except EngineShapeError as error:
        _shape_error(error)

    _configured()
    try:
        try:
            receipt = await _call(user, "delete", {"scope": "all"})
        except EngineCallError as error:
            if error.code != "retryable_conflict":
                raise
            receipt = await _call(user, "delete", {"scope": "all"})  # a concurrent write moved the epoch: once more
    except EngineCallError as error:
        _http_error(error, "delete")

    cleared = failed = 0
    try:
        chat_sessions = await database_service.get_user_sessions(user.id)
    except Exception:  # noqa: BLE001 - the erasure already committed; an unknown session list is a failed clear
        logger.exception("forget_chat_sessions_unlisted")
        chat_sessions, failed = [], 1
    for chat_session in chat_sessions:
        try:
            await agent.clear_chat_history(chat_session.id)
            await database_service.advance_history_epoch(chat_session.id)
            cleared += 1
        except Exception:  # noqa: BLE001 - the erasure already committed; count the failure and report it
            failed += 1
            logger.exception("forget_chat_session_clear_failed", cleared=cleared, failed=failed)

    checks = ForgetChecks(chat_history_cleared=failed == 0)
    try:
        checks.memory_list_empty = view_from_query(await _call(user, "query", _current_state(user))).is_empty()
    except EngineCallError, EngineShapeError:
        logger.warning("forget_check_failed", check="memory_list_empty")
    try:
        manifest = await _call(user, "export", {"selectors": [_own_scope(user)]})
        checks.export_artifact_count = int(manifest["artifact_count"])
    except EngineCallError, KeyError, TypeError, ValueError:
        logger.warning("forget_check_failed", check="export_artifact_count")
    if probe is not None:
        try:
            await _call(user, "get", {"artifact_id": probe})
            checks.old_item_not_found = False
        except EngineCallError as error:
            checks.old_item_not_found = True if error.code == "forbidden" else None

    try:
        counts = deletion_counts(receipt)
    except EngineShapeError as error:
        _shape_error(error)
    logger.info(
        "memory_forget_completed",
        deleted_versions=counts.deleted_versions,
        cleared_chat_sessions=cleared,
        failed_chat_sessions=failed,
        memory_list_empty=checks.memory_list_empty,
        export_artifact_count=checks.export_artifact_count,
    )
    return ForgetResult(receipt=counts, cleared_chat_sessions=cleared, failed_chat_sessions=failed, checks=checks)


# -- routes on one artifact ----------------------------------------------------------------------------------------


@router.get("/{artifact_id}", response_model=MemoryItemResponse)
@limiter.limit(LIMIT)
async def get_memory(request: Request, artifact_id: str, user: User = Depends(get_current_user)) -> MemoryItemResponse:
    """One item. An id that does not exist and an id of another user both answer 404 ``forbidden``."""
    result = await engine(user, "get", {"artifact_id": artifact_id})
    try:
        return MemoryItemResponse(**item_from_artifact(result).model_dump())
    except EngineShapeError as error:
        _shape_error(error)


@router.get("/{artifact_id}/provenance", response_model=ProvenanceOut)
@limiter.limit(LIMIT)
async def memory_provenance(
    request: Request,
    artifact_id: str,
    depth: int = Query(1, ge=1, le=MAX_PROVENANCE_DEPTH),
    user: User = Depends(get_current_user),
) -> ProvenanceOut:
    """Where an item came from: provenance references to ``depth`` hops. References only, never source content."""
    result = await engine(user, "provenance", {"artifact_id": artifact_id, "depth": depth})
    try:
        return provenance_from_view(result)
    except EngineShapeError as error:
        _shape_error(error)


@router.post("/{artifact_id}/adjudicate", response_model=AdjudicateResult)
@limiter.limit(LIMIT)
async def adjudicate_memory(
    request: Request, artifact_id: str, decision: AdjudicateIn, user: User = Depends(get_current_user)
) -> AdjudicateResult:
    """Confirm (becomes ``asserted``) or deny (becomes ``retired``) a ``contested`` claim. Nothing else promotes one."""
    arguments: dict[str, Any] = {"artifact_id": artifact_id, "decision": decision.decision}
    if decision.note is not None:
        arguments["note"] = decision.note
    receipt = await engine(user, "adjudicate", arguments)
    try:
        return AdjudicateResult(
            id=str(receipt["id"]), effect=str(receipt["effect"]), recorded_at=receipt["recorded_at"]
        )
    except KeyError, ValueError:
        _shape_error(EngineShapeError("adjudicate_receipt"))
