"""The HTTP host: ``POST /v1/call`` over the engine's own operations (``E-046``, ``V2-D93``, ``#251``, ``#255``).

**The contract is the engine's, not the host's.** There is no host-side endpoint per operation, no host-side argument
validation and no host-side authorisation rule: the body names an operation, the engine's own contract decides whether
that operation exists, which tier may call it, which arguments it accepts and which conditions must hold. The host adds
transport concerns:

1. **Who is calling** — a bearer token maps to a :class:`~custom_memory_engine.api.entry.Credential`, and the tier
   comes from the token. A top-level ``tier`` field in the envelope is ignored by :func:`_envelope`, while
   ``arguments["tier"]`` is refused by :meth:`Engine.call` as a reserved argument, so the transport cannot be talked
   into a promotion.
2. **A size bound** — a declared maximum body, enforced before parsing. Without it the JSON parser is the DoS surface.
3. **Rate & concurrency limits** — per-principal token bucket and bounded in-flight concurrency
   (``429 resource_limit``) so one saturated caller cannot monopolize all pool connections.
4. **A status code** — the seven codes of ``API_V2.md`` §1.1 mapped to HTTP, with nothing added to the body beyond the
   code and its static sub-code. ``forbidden`` answers 403 for "absent" and "unreadable" alike, so the transport does
   not re-introduce the oracle the engine's error contract removes (``THREAT_MODEL.md`` T-11).
5. **Operational probes** — ``GET /healthz`` and ``GET /metrics`` are unauthenticated, content/tenant-free, and bypass
   the request concurrency semaphore via reserved pool capacity (``pool_max >= max_inflight + 1``).

**What is never logged.** Operation, tier, status, error code and duration — never an argument, principal, token,
request id derived from content, or a response body. The structlog event names are lowercase_with_underscores and the
values are all enums or numbers, which is what makes the log safe to ship.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from custom_memory_engine.api.entry import EntryError
from custom_memory_engine.host.metrics import PROMETHEUS_CONTENT_TYPE
from custom_memory_engine.host.service import HostService

logger = structlog.get_logger(__name__)

#: The seven codes of ``API_V2.md`` section 1.1 and the status each becomes. Deliberately a total mapping: a new code
#: added to the contract without a line here is a ``KeyError`` at start-up (see :func:`_status_for`), not a silent 500.
STATUS_BY_CODE: dict[str, int] = {
    "validation_error": 400,
    "forbidden": 403,
    "precondition_failed": 412,
    "resource_limit": 429,
    "retryable_conflict": 409,
    "internal_error": 500,
}
#: `rejected` is a *result*, not an exception (a gate decision), so it leaves as 200 with the decision in the body.
#: It appears here only to keep the mapping total over the contract's vocabulary.
_RESULT_CODES = frozenset({"rejected"})


def create_app(service: HostService) -> Starlette:
    """Build the ASGI application around an opened :class:`HostService`."""

    async def healthz(_request: Request) -> Response:
        """Unauthenticated and content-free: verifies liveness and DB connectivity using reserved pool capacity."""
        if not await service.probe_health():
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    async def metrics(_request: Request) -> Response:
        """Unauthenticated Prometheus v0.0.4 scrape endpoint; never carries tenant ids or caller content."""
        queue_counts = await service.collect_queue_metrics()
        payload = service.metrics.render(queue_counts=queue_counts)
        return Response(payload, media_type=PROMETHEUS_CONTENT_TYPE)

    async def call(request: Request) -> Response:
        started = time.perf_counter()
        request_id = f"req_{time.perf_counter_ns():x}"  # process-scoped, never derived from content
        credential = service.credentials.authenticate(request.headers.get("authorization"))
        if credential is None:
            # 401 with no body detail: whether a token exists and whether it is close are not questions this answers.
            return _error(
                "forbidden",
                request_id,
                status=401,
                started=started,
                operation=None,
                tier=None,
                service=service,
            )
        try:
            body = await _body(request, service.settings.max_body_bytes)
            operation, arguments = _envelope(body)
        except _BadRequest as refusal:
            return _error(
                refusal.code,
                request_id,
                status=400,
                started=started,
                operation=None,
                tier=credential.tier,
                service=service,
            )
        try:
            result = await service.call(credential, operation, arguments)
        except EntryError as error:
            return _error(
                error.code,
                request_id,
                status=_status_for(error.code),
                started=started,
                operation=operation,
                tier=credential.tier,
                detail=error.detail,
                service=service,
            )
        except Exception as error:  # noqa: BLE001 - the last resort, and it must not disclose the exception
            logger.error("host_call_crashed", operation=operation, error_type=type(error).__name__)
            return _error(
                "internal_error",
                request_id,
                status=500,
                started=started,
                operation=operation,
                tier=credential.tier,
                service=service,
            )
        duration_seconds = time.perf_counter() - started
        service.metrics.record_call(
            operation=operation,
            tier=credential.tier,
            status=200,
            duration_seconds=duration_seconds,
        )
        logger.info(
            "host_call",
            operation=operation,
            tier=credential.tier,
            status=200,
            duration_ms=round(duration_seconds * 1000, 2),
        )
        return JSONResponse({"result": _jsonable(result)}, headers={"x-request-id": request_id})

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        """Own the pool: opened on start-up, closed on shutdown. `start` is idempotent, so a test that opened the
        service itself does not open a second pool."""
        await service.start()
        try:
            yield
        finally:
            await service.close()

    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/metrics", metrics, methods=["GET"]),
            Route("/v1/call", call, methods=["POST"]),
        ],
        lifespan=lifespan,
        # No `/docs`, no OpenAPI: the contract is `API_V2.md`, and a generated schema is a second source of truth.
    )


class _BadRequest(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _body(request: Request, limit: int) -> Any:
    """Read and parse the body, bounded. ``Content-Length`` is checked first and the stream is then capped as well."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise _BadRequest("request_too_large")
        except ValueError:
            raise _BadRequest("content_length_invalid") from None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _BadRequest("request_too_large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _BadRequest("body_not_json") from None


def _envelope(body: Any) -> tuple[str, dict[str, object]]:
    """``{operation, arguments}``. Nothing else is read: an unknown body field cannot influence a call."""
    if not isinstance(body, dict):
        raise _BadRequest("body_must_be_an_object")
    operation = body.get("operation")
    if not isinstance(operation, str) or not operation:
        raise _BadRequest("operation_required")
    arguments = body.get("arguments", {})
    if not isinstance(arguments, dict):
        raise _BadRequest("arguments_must_be_an_object")
    return operation, arguments


def _status_for(code: str) -> int:
    if code in STATUS_BY_CODE:
        return STATUS_BY_CODE[code]
    if code in _RESULT_CODES:  # pragma: no cover - defensive: a result code must never arrive as an exception
        return 200
    # A code the contract does not define is an engine defect. Answer the last-resort code rather than inventing one.
    logger.error("host_unknown_error_code", error_code=code[:32])
    return 500


def _error(
    code: str,
    request_id: str,
    *,
    status: int,
    started: float,
    operation: str | None,
    tier: str | None,
    detail: str | None = None,
    service: HostService | None = None,
) -> JSONResponse:
    """A static error body: the code, its sub-code when it is one, and a request id. Never a message."""
    payload: dict[str, object] = {"error": {"code": code}}
    if detail is not None and _is_static_code(detail):
        payload["error"] = {"code": code, "detail": detail}  # type: ignore[dict-item]
    duration_seconds = time.perf_counter() - started
    if service is not None:
        service.metrics.record_call(
            operation=operation,
            tier=tier,
            status=status,
            duration_seconds=duration_seconds,
            rate_limited=(status == 429),
        )
    logger.info(
        "host_call",
        operation=operation,
        tier=tier,
        status=status,
        error_code=code,
        duration_ms=round(duration_seconds * 1000, 2),
    )
    return JSONResponse(payload, status_code=status, headers={"x-request-id": request_id})


def _jsonable(value: Any) -> Any:
    """Serialise an operation's result without inventing a second schema.

    Results are the engine's own frozen dataclasses (``Receipt``, ``QueryResult``, ...), so the wire shape is derived
    from them rather than hand-mapped: a field added to a result is on the wire the moment it exists, and cannot be
    forgotten here. ``Decimal`` becomes ``str`` (never ``float``: a cost is money) and a ``datetime`` becomes ISO-8601.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    slots = getattr(type(value), "__slots__", None)
    if isinstance(slots, tuple | list) and slots:
        # `Artifact`, `QueryResult` and friends are frozen `__slots__` classes (they predate the dataclass style in
        # this codebase). Reading the declared slots is the same "derive, never hand-map" rule as the dataclass
        # branch, and it fails loudly (an unset slot is a programming error) rather than silently dropping a field.
        return {name: _jsonable(getattr(value, name)) for name in slots if not name.startswith("_")}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _is_static_code(value: str) -> bool:
    """A sub-code is a declared identifier. Anything else could be a value from the caller's text and is dropped."""
    return 0 < len(value) <= 64 and all(
        character.islower() or character.isdigit() or character == "_" for character in value
    )


__all__ = ["STATUS_BY_CODE", "create_app"]
