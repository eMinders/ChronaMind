# Tickets — Epic: ChronaMind Studio demo interface — Label: app

Side: `apps/api/app/**`, `apps/api/evals/**`, `apps/api/alembic/**`, `apps/api/docs/**`, and the root `scripts/` and `apps/api/tests/` that run on the app's
interpreter (CPython 3.14). Verify with `make check` at the repository root.

## References

| Source | Path |
|---|---|
| Parent Epic | [`tickets/EPIC-demo-interface.md`](EPIC-demo-interface.md) |
| App rules | [`AGENTS.md`](../AGENTS.md), [`CLAUDE.md`](../CLAUDE.md) |
| App memory client | [`apps/api/app/services/memory_engine.py`](../app/services/memory_engine.py) |
| Live boundary test recipe | [`apps/api/tests/integration/test_app_engine_boundary.py`](../apps/api/tests/integration/test_app_engine_boundary.py) |
| Engine API reference | [`core/memory-engine/PLAN/V2/API_V2.md`](../core/memory-engine/PLAN/V2/API_V2.md) §4.1–4.9 |
| Delegating tokens | [`core/memory-engine/PLAN/V2/DECISIONS_V2.md`](../core/memory-engine/PLAN/V2/DECISIONS_V2.md) `V2-D120`; `THREAT_MODEL.md` `T-42` |

---

## [APP-01] · One-command live demo stack

**Label:** app, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** none
**Blocks:** APP-06, APP-08

### Objective

Add `tools/demo/up.py`, `down.py` and `check.py`. One command starts the engine database, the engine host with two
delegating tokens, the app database and the app, and a second command proves the memory path end to end. Every later
ticket is accepted on this stack.

### Background and Current State

- The engine host runs from `core/memory-engine/scripts/run_host.py` and needs `CME_DATABASE_URL`, `CME_RUNTIME_ROLE`,
  `CME_SCHEMA`, `CME_HOST_TOKENS`, `CME_PROVENANCE_KEY` and `CME_DELETION_RECEIPT_KEY`.
- `apps/api/tests/integration/test_app_engine_boundary.py` (fixture `engine_host`) is the only proven recipe: migrate a schema
  with `python -m custom_memory_engine.schema.migrate apply --schema <s>` (env `RL_DATABASE_URL`), provision a role with
  `scripts/provision_role.py --schema <s> --role <r>`, then start the host lexical-only. No model files are needed.
- The engine's interpreter is `core/memory-engine/.venv`, which is `.venv/bin/python` on Linux and macOS and
  `.venv/Scripts/python.exe` on Windows.
- `.claude/skills/run-chronamind/` starts only the app, with no engine. On Windows with Avast it needs
  `SSLKEYLOGFILE` removed, a CA bundle shim, and the Selector event loop, which `run.py` sets.
- `RATE_LIMIT_REGISTER` defaults to 10 per hour, which repeated runs exceed.

### Acceptance Criteria

- `uv run python tools/demo/up.py` from cold reaches a printed "all healthy" line in under 3 minutes, with app
  `/health` and host `/healthz` both answering 200.
- The host runs with an agent token (Tier B, delegating) and an inspector token (Tier C, delegating), both generated
  into the gitignored `tools/demo/.env.demo` on the first run and reused on every later run.
- `CME_PROVENANCE_KEY` and `CME_DELETION_RECEIPT_KEY` keep the same values across `down.py` / `up.py` cycles until
  `down.py --wipe` removes the engine volume.
- `tools/demo/check.py` registers a new user, chats "I live in Berlin", then "I moved to Paris", and asserts that a
  `query` as that user returns exactly one non-episode fact containing "Paris" within 20 seconds. It passes 3
  consecutive runs.
- `down.py` stops every process `up.py` started. No process from the stack remains listening on 8000 or 8100.
- No token, key or database URL is printed to the console or written outside `tools/demo/.env.demo` and
  `tools/demo/.state/`. Both are ignored by `tools/demo/.gitignore`, and `git status` stays clean after a run.
- The app is started with `MEMORY_ENGINE_URL`, `MEMORY_ENGINE_TOKEN`, `MEMORY_ENGINE_USER_TOKEN` and
  `RATE_LIMIT_REGISTER=1000 per hour` taken from `.env.demo`.
- `tools/demo/README.md` lists the prerequisites (`uv sync`, `uv sync --locked --extra host` in the engine, Docker)
  and the three commands. The PR states the operating systems it was run on.

### Implementation

```python
"""Start the ChronaMind demo stack: engine database, engine host, app database, app."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENGINE = ROOT / "core/memory-engine"
ENGINE_PYTHON = ENGINE / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
STATE, ENV_FILE = HERE / ".state", HERE / ".env.demo"
SCHEMA, ROLE = "cme_demo", "cme_demo_rt"


def demo_secrets() -> dict[str, str]:
    """Generate the tokens and keys once; every later run reuses them (the keys must survive restarts)."""
    if ENV_FILE.exists():
        return dict(line.split("=", 1) for line in ENV_FILE.read_text().splitlines() if "=" in line)
    names = ("AGENT_TOKEN", "INSPECTOR_TOKEN", "PROVENANCE_KEY", "RECEIPT_KEY")
    values = {name: secrets.token_urlsafe(32) for name in names}
    ENV_FILE.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
    return values


def engine_environment(database_url: str, values: dict[str, str]) -> dict[str, str]:
    """The host's environment: inherited variables minus any CME_/RL_ setting, plus the demo's own."""
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(("CME_", "RL_"))}
    tokens = {
        values["AGENT_TOKEN"]: {"principal": "chronamind-app", "tier": "B", "delegate": True},
        values["INSPECTOR_TOKEN"]: {"principal": "chronamind-inspector", "tier": "C", "delegate": True},
    }
    return {
        **inherited, "RL_DATABASE_URL": database_url, "CME_DATABASE_URL": database_url,
        "CME_RUNTIME_ROLE": ROLE, "CME_SCHEMA": SCHEMA, "CME_HOST_TOKENS": json.dumps(tokens),
        "CME_PROVENANCE_KEY": values["PROVENANCE_KEY"], "CME_DELETION_RECEIPT_KEY": values["RECEIPT_KEY"],
        "CME_HOST_HOST": "127.0.0.1", "CME_HOST_PORT": "8100",
    }


def prepare_engine(environment: dict[str, str]) -> None:
    """Migrate the demo schema and provision its runtime role with the engine's own interpreter."""
    migrate = ["-m", "custom_memory_engine.schema.migrate", "apply", "--schema", SCHEMA]
    for arguments in (migrate, ["scripts/provision_role.py", "--schema", SCHEMA, "--role", ROLE]):
        subprocess.run([str(ENGINE_PYTHON), *arguments], cwd=ENGINE, env=environment, check=True)
```

### Files

```text
tools/demo/
├── up.py           # NEW
├── down.py         # NEW
├── check.py        # NEW
├── README.md       # NEW
└── .gitignore      # NEW (.env.demo, .state/)
```

### Verification

```bash
uv run python tools/demo/up.py
uv run python tools/demo/check.py      # three consecutive passes
uv run python tools/demo/down.py
make check
```

### Dependencies

- Depends on: none.
- Blocks: APP-06, APP-08. Every live acceptance criterion in the other tickets runs on this stack.
- External: Docker; the engine's locked environment with the `host` extra.

---

## [APP-02] · Inspector token and typed engine call in the app client

**Label:** app, feature, security
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** none
**Blocks:** APP-03, APP-06

### Objective

Add the setting `MEMORY_ENGINE_USER_TOKEN` and a second client instance, `memory_inspector`, in
`apps/api/app/services/memory_engine.py`. It uses that Tier C delegating token and exposes `call(user_id, operation,
arguments)`, which raises a typed `EngineCallError` carrying the engine's static code. The agent path keeps its Tier B
token.

### Background and Current State

- `MemoryEngineClient._http()` always reads `settings.MEMORY_ENGINE_TOKEN`. `_post()` calls `raise_for_status()`,
  so the engine's error code is lost.
- `search()` and `add()` swallow every failure on purpose (memory is an enhancement on the agent path). The inspector
  path needs errors surfaced.
- The host accepts a delegating Tier C token. A delegated credential keeps the token's tier and is never admin
  (`host/credentials.py`, `V2-D120`). `adjudicate`, `delete` and `export` are Tier C only (`api/contract.py` TB1, TB2,
  TB9).
- Host statuses: `validation_error` 400, `forbidden` 403, `precondition_failed` 412, `resource_limit` 429,
  `retryable_conflict` 409, `internal_error` 500. The body is `{"error": {"code", "detail"?}}`.
- `apps/api/app/main.py` closes `memory_engine` in the lifespan shutdown.

### Acceptance Criteria

- `settings.MEMORY_ENGINE_USER_TOKEN` reads the environment variable of the same name, defaulting to `""`. It is
  documented with a placeholder in `.env.example` and in the configuration table of `apps/api/docs/memory.md`.
- The inspector client sends `Authorization: Bearer <MEMORY_ENGINE_USER_TOKEN>`. The agent client still sends
  `MEMORY_ENGINE_TOKEN`, and a test fails if the agent client ever sends the inspector token.
- `memory_inspector.call` returns the `result` object on HTTP 200.
- On 400, 403, 409, 412, 429 and 500 it raises `EngineCallError` whose `code` equals the engine's code and whose
  `status` equals the HTTP status.
- A transport error raises `EngineCallError` with code `engine_unavailable` and status 0.
- The inspector client uses a 15-second read timeout. The agent client keeps 5 seconds.
- `memory_inspector` is closed in the lifespan shutdown next to `memory_engine`.
- Live check on the APP-01 stack: `delete` with `{"scope": "all"}` for a fresh principal returns 200 with the
  inspector token, and `forbidden` with the agent token. Both outputs are in the PR.

### Implementation

```python
class EngineCallError(Exception):
    """An engine refusal or an unreachable host: the engine's static code, its detail, the HTTP status (0 = none)."""

    def __init__(self, code: str, detail: str | None, status: int) -> None:
        super().__init__(code)
        self.code, self.detail, self.status = code, detail, status


class MemoryEngineClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        slot_classifier: SlotClassifier | None = None,
        token_setting: str = "MEMORY_ENGINE_TOKEN",
        read_timeout: float = 5.0,
    ) -> None:
        self._transport, self._client = transport, None
        self._slot_classifier = slot_classifier if slot_classifier is not None else configured_classifier()
        self._token_setting, self._read_timeout = token_setting, read_timeout

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            token = getattr(settings, self._token_setting, "")
            self._client = httpx.AsyncClient(
                base_url=settings.MEMORY_ENGINE_URL.rstrip("/"),
                timeout=httpx.Timeout(self._read_timeout, connect=2.0),
                headers={"Authorization": f"Bearer {token}"} if token else {},
                transport=self._transport,
            )
        return self._client

    async def call(self, user_id: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One operation acting for ``user_id``; refusals raise :class:`EngineCallError` instead of being swallowed."""
        envelope = {"operation": operation, "principal": user_id, "arguments": arguments}
        try:
            response = await self._http().post("/v1/call", json=envelope)
        except httpx.TransportError:
            raise EngineCallError("engine_unavailable", None, 0) from None
        body = response.json() if response.content else {}
        if response.status_code != 200:
            error = body.get("error") or {}
            raise EngineCallError(str(error.get("code", "internal_error")), error.get("detail"), response.status_code)
        if not isinstance(body.get("result"), dict):
            raise EngineCallError("internal_error", "response_without_result", response.status_code)
        return body["result"]


memory_inspector = MemoryEngineClient(
    slot_classifier=NullSlotClassifier(), token_setting="MEMORY_ENGINE_USER_TOKEN", read_timeout=15.0
)
```

### Files

```text
app/
├── core/config.py                    # MODIFIED
├── main.py                           # MODIFIED (close memory_inspector)
└── services/memory_engine.py         # MODIFIED
apps/api/docs/memory.md                        # MODIFIED
.env.example                          # MODIFIED (placeholder only)
apps/api/tests/test_memory_inspector_client.py # NEW
```

### Verification

```bash
cd apps/api && uv run pytest -q apps/api/tests/test_memory_inspector_client.py apps/api/tests/test_memory_engine_boundary.py
make check
```

### Dependencies

- Depends on: none (the live check uses APP-01).
- Blocks: APP-03, APP-06.
- External: none.

---

## [APP-03] · Memory router, part 1: health, list, search, get, provenance

**Label:** app, feature, security
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-02
**Blocks:** APP-04, APP-05, APP-07, CI-02, WEB-05

### Objective

Add `apps/api/app/api/v1/memory.py`, mounted at `/api/v1/memory`, with response models in `apps/api/app/schemas/memory.py` and pure
mapping functions in `apps/api/app/services/memory_view.py`. The routes are `GET /health`, `GET /`, `POST /search`,
`GET /{artifact_id}` and `GET /{artifact_id}/provenance`. Each acts as the signed-in user through `memory_inspector`,
and each is proven against a real engine host. The response models are the single source of the web app's generated
types.

### Background and Current State

- The engine returns a `QueryResult` with channels `facts`, `hypotheses`, `procedures` and `conflicts`. Each item
  carries `artifact_id`, `kind`, `lifecycle`, `channel`, `content`, `verification`, `label`, `support_summary`,
  `bound`, `conflict_sides`, `provenance`, `scope_path` and `hop_distance`. There are no timestamps and no quotes.
- The `facts` channel also carries episodes (verbatim turns). `memory_engine._render` separates them by `kind`.
- A provenance reference is readable (`source_id`, `scope_path`, `source_type`, `derivation_type`, `readable`) or opaque
  (`opaque_ref`, `derivation_type`, `readable: false`). `provenance` returns `artifact_id`, `depth`, `edges[{depth,
  reference}]` and `depth_limited`.
- `query` accepts `limit` from 1 to 1000. Listing everything sends no `text`.
- The required models and their fields:
  - `SourceRefOut`: `readable`, `source_type`, `derivation_type`, `ref`.
  - `MemoryItem`: `id`, `kind`, `lifecycle`, `channel`, `content`, `verification`, `label`, `support_summary`,
    `sources`.
  - `MemoryView`: `known_facts`, `hypotheses`, `procedures`, `conflicts`, `conversation`, plus the methods
    `all_items()` and `is_empty()`.
  - `ProvenanceOut`: `id`, `depth`, `depth_limited`, `edges`.
  - `EngineHealth`: `engine` in `up`, `down`, `not_configured`.
- Routes use `@limiter.limit(settings.RATE_LIMIT_ENDPOINTS[...][0])` like `chatbot.py`. Adding the key `"memory"` to
  `default_endpoints` in `config.py` makes `RATE_LIMIT_MEMORY` configurable.

### Acceptance Criteria

- Every route except `/health` requires the user bearer token.
- `principal` is always `str(user.id)`, and a `principal`, `user_id` or `tier` field in the query or body has no
  effect (unit test).
- `GET /api/v1/memory/` sends `query` with `{"mode": "current_state", "scope": "/u/<id>/", "limit": <1..1000>}` and no
  `text`. No item of kind `episode` appears outside `conversation`.
- `POST /search` with `text` shorter than 2 or longer than 300 characters returns HTTP 422 without calling the
  engine.
- No response body contains `scope_path` (unit test on all routes).
- `GET /{artifact_id}` for an id owned by another user returns HTTP 404 with code `forbidden` (live test).
- An engine refusal produces `{"detail": {"code": <engine code>, "detail": <detail or null>}}` with HTTP status
  `validation_error` 422, `forbidden` 404, `precondition_failed` 409, `resource_limit` 429, `internal_error` 502 and
  `engine_unavailable` 502. An empty inspector token returns 503 with code `inspector_not_configured`.
- Live test in `apps/api/tests/integration/test_memory_router_live.py`, with a host configured with both delegating tokens:
  after `memory_engine.add()` of "I live in Berlin" then "I moved to Paris" (recorded classifications from
  `apps/api/tests/slot_fakes.py`), `GET /` returns exactly one `known_facts` item containing "Paris", and `conversation`
  contains both sentences.
- Live test: `GET /{id}` of the Paris claim returns `lifecycle` `asserted`, `channel` `facts`, and a readable source
  whose `ref` starts with `app://session/`. `GET /{id}/provenance?depth=1` returns 200.
- The live test resolves the engine interpreter per platform (`.venv/Scripts/python.exe` on Windows) and skips with a
  reason when it or `CME_TEST_DATABASE_URL` is missing.

### Implementation

```python
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.memory import MemoryItem, MemoryView, SearchIn
from app.services.memory_engine import EngineCallError, memory_inspector
from app.services.memory_view import item_from_artifact, view_from_query

router = APIRouter()
LIMIT = settings.RATE_LIMIT_ENDPOINTS["memory"][0]
_STATUS = {"validation_error": 422, "forbidden": 404, "precondition_failed": 409, "resource_limit": 429,
           "retryable_conflict": 409, "internal_error": 502, "engine_unavailable": 502}


async def engine(user: User, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One inspector call acting as ``user``; a refusal becomes an HTTP error carrying the engine's static code."""
    if not settings.MEMORY_ENGINE_URL or not settings.MEMORY_ENGINE_USER_TOKEN:
        raise HTTPException(503, detail={"code": "inspector_not_configured", "detail": None})
    try:
        return await memory_inspector.call(str(user.id), operation, arguments)
    except EngineCallError as error:
        raise HTTPException(_STATUS.get(error.code, 502), detail={"code": error.code, "detail": error.detail}) from None


@router.get("/", response_model=MemoryView)
@limiter.limit(LIMIT)
async def list_memory(request: Request, limit: int = Query(200, ge=1, le=1000), user: User = Depends(get_current_user)):
    body = {"mode": "current_state", "scope": f"/u/{user.id}/", "limit": limit}
    return view_from_query(await engine(user, "query", {"request": body}))


@router.post("/search", response_model=MemoryView)
@limiter.limit(LIMIT)
async def search_memory(request: Request, search: SearchIn, user: User = Depends(get_current_user)):
    body = {"mode": "current_state", "scope": f"/u/{user.id}/", "text": search.text, "limit": 20}
    return view_from_query(await engine(user, "query", {"request": body}))


@router.get("/{artifact_id}", response_model=MemoryItem)
@limiter.limit(LIMIT)
async def get_memory(request: Request, artifact_id: str, user: User = Depends(get_current_user)):
    return item_from_artifact(await engine(user, "get", {"artifact_id": artifact_id}))
```

### Files

```text
app/
├── api/v1/api.py                          # MODIFIED (include the memory router at /memory)
├── api/v1/memory.py                       # NEW
├── core/config.py                         # MODIFIED ("memory" rate-limit key)
├── schemas/memory.py                      # NEW
└── services/memory_view.py                # NEW
tests/
├── test_memory_router.py                  # NEW
├── test_memory_view.py                    # NEW
└── integration/test_memory_router_live.py # NEW
```

### Verification

```bash
cd apps/api && uv run pytest -q apps/api/tests/test_memory_router.py apps/api/tests/test_memory_view.py
CME_TEST_DATABASE_URL=<placeholder-dsn> cd apps/api && uv run pytest -q apps/api/tests/integration/test_memory_router_live.py
make check
```

### Dependencies

- Depends on: APP-02.
- Blocks: APP-04, APP-05, APP-07, CI-02, WEB-05.
- External: none.

---

## [APP-04] · Memory router, part 2: timeline and changes

**Label:** app, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-03
**Blocks:** WEB-09

### Objective

Add `GET /api/v1/memory/timeline?at=` and `GET /api/v1/memory/changes?start=&end=`. Timeline returns what the engine
believed at an instant, through `query` mode `audit_as_of`. Changes returns what differs between two instants, through
`query` mode `change`. Both back the Time Travel screen.

### Background and Current State

- `audit_as_of` requires `target_time` and `belief_time` and is Tier C only. Its rows come back in `audit`, not in
  the channels (`api/read_handlers.py`).
- `audit_as_of` and `change` refuse `text` with `validation_error` / `text_unsupported_for_mode` (`V2-D86`).
- `change` requires `window_start` and `window_end`. Its `changes[]` entries are `{outcome, previous[], current[]}`
  with version ids only, and the artifacts are in the same result's channels (`engine/change.py`, `V2-D81`).
- A request carrying `reference_time` is refused (`I4`, `V2-D73`).
- Models to add: `TimelineView` (`at`, `beliefs`, `conversation`), `ChangeOut` (`outcome`, `previous`, `current` as
  `MemoryItem` lists), `ChangesView` (`start`, `end`, `changes`).

### Acceptance Criteria

- `GET /timeline?at=T` sends `{"mode": "audit_as_of", "scope": "/u/<id>/", "target_time": T, "belief_time": T,
  "limit": 200}` with no `text` and no `reference_time`.
- `at`, `start` or `end` without a timezone offset, in the future, or with `start >= end` returns HTTP 422 without
  calling the engine.
- `/timeline` and `/changes` are declared before `/{artifact_id}`, so `GET /timeline` never reaches the `get` handler
  (unit test).
- Every change id is resolved to a `MemoryItem` from the same result. An id absent from the result is omitted, and
  the response is still 200.
- Live test: with instants t0 (before Berlin), t1 (after the Berlin write, before Paris) and t2 (after the Paris
  write):
  - `beliefs` at t0 is empty;
  - at t1 it contains a claim with "Berlin" and none with "Paris";
  - at t2 it contains "Paris" and no Berlin claim.
- Live test: `GET /changes?start=t1&end=t2` returns at least one change. The PR records the observed `outcome` values.

### Implementation

```python
from datetime import UTC, datetime

from fastapi import HTTPException

from app.schemas.memory import ChangesView, TimelineView
from app.services.memory_view import changes_from_query, timeline_from_query


def past_instant(value: datetime, name: str) -> str:
    """An aware instant not in the future, as ISO-8601; the engine checks it again against its own clock."""
    if value.tzinfo is None:
        raise HTTPException(422, detail={"code": "validation_error", "detail": f"{name}_needs_timezone"})
    if value > datetime.now(UTC):
        raise HTTPException(422, detail={"code": "validation_error", "detail": f"{name}_in_future"})
    return value.astimezone(UTC).isoformat()


@router.get("/timeline", response_model=TimelineView)
@limiter.limit(LIMIT)
async def timeline(request: Request, at: datetime, user: User = Depends(get_current_user)):
    instant = past_instant(at, "at")
    body = {"mode": "audit_as_of", "scope": f"/u/{user.id}/", "target_time": instant, "belief_time": instant,
            "limit": 200}
    return timeline_from_query(at, await engine(user, "query", {"request": body}))


@router.get("/changes", response_model=ChangesView)
@limiter.limit(LIMIT)
async def changes(request: Request, start: datetime, end: datetime, user: User = Depends(get_current_user)):
    window_start, window_end = past_instant(start, "start"), past_instant(end, "end")
    if start >= end:
        raise HTTPException(422, detail={"code": "validation_error", "detail": "window_must_be_nonempty"})
    body = {"mode": "change", "scope": f"/u/{user.id}/", "window_start": window_start, "window_end": window_end,
            "limit": 200}
    return changes_from_query(start, end, await engine(user, "query", {"request": body}))
```

### Files

```text
app/
├── api/v1/memory.py                       # MODIFIED
├── schemas/memory.py                      # MODIFIED
└── services/memory_view.py                # MODIFIED
tests/
├── test_memory_router.py                  # MODIFIED
├── test_memory_view.py                    # MODIFIED
└── integration/test_memory_router_live.py # MODIFIED
```

### Verification

```bash
cd apps/api && uv run pytest -q apps/api/tests/test_memory_router.py apps/api/tests/test_memory_view.py
CME_TEST_DATABASE_URL=<placeholder-dsn> cd apps/api && uv run pytest -q apps/api/tests/integration/test_memory_router_live.py
make check
```

### Dependencies

- Depends on: APP-03.
- Blocks: WEB-09.
- External: none.

---

## [APP-05] · Memory router, part 3: adjudicate and forget

**Label:** app, feature, security
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-03
**Blocks:** APP-06, WEB-08, WEB-10

### Objective

Add `POST /api/v1/memory/{artifact_id}/adjudicate` and `POST /api/v1/memory/forget`. Adjudicate lets the user confirm
or deny a `contested` claim. Forget erases everything the engine holds for the user with `delete` scope `all`, clears
every chat session of that user in the app, and returns counts plus four observed after-checks.

### Background and Current State

- `adjudicate` takes `artifact_id`, `decision` (`confirm` or `deny`) and `note`. It returns a `Receipt` (`id`,
  `effect`, `recorded_at`), where `effect` is `asserted` for confirm and `retired` for deny. A target that is no longer
  contested is `precondition_failed` (`api/handlers.py`).
- `delete` takes `scope` in `memory`, `topic`, `all`. Scope `memory` matches a `memory_key` that no response exposes,
  so this ticket uses `all`. It returns a `DeletionReceipt` with `deletion_epoch`, `deleted_versions`, `dependents`,
  `rederived_dependents`, `retired_dependents`, `deleted_turns`, `deleted_entities`, `deleted_mentions`,
  `purged_jobs`, `request_id`, `external_exclusions` (`engine/deletion.py`).
- `export` with `selectors` returns a manifest with `artifact_count` and no items (`engine/export.py`, `V2-D99`).
- The chat transcript lives in the app's LangGraph checkpointer, and the agent reads the whole session every turn.
  `DELETE /chatbot/messages` clears one session with `agent.clear_chat_history(session_id)` plus
  `database_service.advance_history_epoch(session_id)`. `database_service.get_user_sessions(user_id)` lists sessions.
- Models to add: `AdjudicateIn`, `AdjudicateResult`, `ForgetIn` (`confirm: Literal["DELETE"]`), `DeletionCounts`,
  `ForgetChecks`, `ForgetResult`.

### Acceptance Criteria

- `POST /forget` with any `confirm` other than `"DELETE"` returns HTTP 422 and calls nothing.
- Forget calls `delete` with `{"scope": "all"}` before clearing any chat session. When `delete` fails, no session is
  cleared and the engine code is returned (unit test).
- A `retryable_conflict` from `delete` is retried once; a second `retryable_conflict` returns HTTP 409.
- Every session of the signed-in user, and no session of another user, is cleared, and its history epoch advanced
  (unit test with two users).
- The response contains counts only: no `request_id`, no `external_exclusions`, no content (unit test).
- Live test: after Berlin → Paris, forget returns `deleted_versions > 0`, `memory_list_empty: true` and
  `old_item_not_found: true`, and `memory_engine.search(user, "Where do I live?")` returns `""`.
- Live test: forget reports `export_artifact_count` 0. If the engine reports another value, the PR stops and reports
  it, and the UI wording is not changed to match.
- Live test: a claim asserted with `"assertion_mode": "hedged"` lists under `conflicts`. `confirm` moves it to
  `known_facts`, and a second adjudicate returns HTTP 409 with code `precondition_failed`.
- Live test: `deny` on a fresh hedged claim removes it from every section.

### Implementation

```python
from app.api.v1.chatbot import agent
from app.core.logging import logger
from app.schemas.memory import DeletionCounts, ForgetChecks, ForgetIn, ForgetResult
from app.services.database import database_service


async def delete_all(user: User) -> dict[str, Any]:
    """`delete` scope `all`, retried once on `retryable_conflict` (a concurrent write moved the epoch)."""
    try:
        return await engine(user, "delete", {"scope": "all"})
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return await engine(user, "delete", {"scope": "all"})


@router.post("/forget", response_model=ForgetResult)
@limiter.limit(LIMIT)
async def forget(request: Request, body: ForgetIn, user: User = Depends(get_current_user)):
    current = {"request": {"mode": "current_state", "scope": f"/u/{user.id}/", "limit": 200}}
    probe = next((item.id for item in view_from_query(await engine(user, "query", current)).all_items()), None)
    receipt = await delete_all(user)
    cleared = 0
    for session in await database_service.get_user_sessions(user.id):
        try:
            await agent.clear_chat_history(session.id)
            await database_service.advance_history_epoch(session.id)
            cleared += 1
        except Exception:  # noqa: BLE001 - the erasure already committed; report the real count, never hide it
            logger.exception("forget_session_clear_failed", cleared=cleared)
    after = view_from_query(await engine(user, "query", current))
    manifest = await engine(user, "export", {"selectors": [f"/u/{user.id}/"]})
    old_item_not_found: bool | None = None
    if probe is not None:
        try:
            await engine(user, "get", {"artifact_id": probe})
            old_item_not_found = False
        except HTTPException as error:
            old_item_not_found = error.status_code == 404
    checks = ForgetChecks(memory_list_empty=after.is_empty(), export_artifact_count=int(manifest["artifact_count"]),
                          old_item_not_found=old_item_not_found)
    return ForgetResult(receipt=DeletionCounts.model_validate(receipt), cleared_chat_sessions=cleared, checks=checks)
```

### Files

```text
app/
├── api/v1/memory.py                       # MODIFIED
└── schemas/memory.py                      # MODIFIED
tests/
├── test_memory_router.py                  # MODIFIED
└── integration/test_memory_router_live.py # MODIFIED
```

### Verification

```bash
cd apps/api && uv run pytest -q apps/api/tests/test_memory_router.py
CME_TEST_DATABASE_URL=<placeholder-dsn> cd apps/api && uv run pytest -q apps/api/tests/integration/test_memory_router_live.py
make check
```

### Dependencies

- Depends on: APP-03.
- Blocks: APP-06, WEB-08, WEB-10.
- External: none.

---

## [APP-06] · Demo seed and reset for the user Layla

**Label:** app, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-01, APP-02, APP-05
**Blocks:** WEB-08, APP-09

### Objective

Add `tools/demo/seed.py` and `tools/demo/reset.py`. Seed creates the demo user Layla through the app's auth API
and writes her memory through the inspector client, using the agent's own request shapes. The result covers known
facts, a superseded value and one contested claim. Reset erases her memory through `POST /api/v1/memory/forget`. The
seeded user carries journey J5 and is the fallback when the LLM is unavailable.

### Background and Current State

- The agent writes memory as `ingest_episode`, then `assert` with `_claim_arguments(scope, text, turn_id,
  classification)` (`apps/api/app/services/memory_engine.py`). Idempotency keys are `episode:<turn_id>` and
  `claim:<turn_id>`.
- `SlotClassification("single", "residence")` supersedes the previous residence. `MULTI` supersedes nothing.
- `assert` with `assertion_mode` `hedged` is held: the receipt says `contested` (`engine/gate.py::classify_assertion`).
  A held claim is readable by its receipt id only; no `query` lists it until it is adjudicated (`API_V2.md` §4.2).
- `POST /api/v1/auth/register` validates `EmailStr`, which rejects special-use domains such as `.local`.
- No hypothesis can be produced without an admin token (`register_method`) and Tier B `infer`. That is outside this
  ticket.

### Acceptance Criteria

- On a fresh APP-01 stack, `seed.py` exits 0. `GET /api/v1/memory/` as `layla.demo@example.com` then returns at least
  3 `known_facts` and the superseded residence only in `conversation`. The held claim's id is printed, and
  `GET /api/v1/memory/<id>` returns it with lifecycle `contested` (it is not listed; see Needs confirmation).
- Running `seed.py` twice in a row yields the same section counts.
- `reset.py` followed by `seed.py` yields the same counts as a first seed.
- `reset.py` prints the deletion counts and the four after-checks returned by `/forget`.
- The password comes from `DEMO_SEED_PASSWORD`. When that is unset, the password is generated and printed once, on the
  run that registers the user.
- No token, key or database URL is printed.
- `tools/demo/README.md` documents both commands.

### Implementation

```python
"""Seed the demo user Layla through public interfaces only: the app's auth API and the engine's operations."""

from __future__ import annotations

from app.services.memory_engine import _claim_arguments, canonical_hash, memory_inspector
from app.services.memory_slots import MULTI, SlotClassification

FACTS: tuple[tuple[str, str | None, str | None], ...] = (
    # (text, semantic slot or None, assertion mode or None)
    ("I live in Alexandria", "residence", None),
    ("I moved to Cairo", "residence", None),
    ("I work at Nile Analytics", "employer", None),
    ("I prefer morning meetings", None, None),
    ("I might switch to a vegetarian diet", None, "hedged"),
)


async def write(user_id: str, position: int, text: str, slot: str | None, mode: str | None) -> str:
    """One turn and its claim, with fixed idempotency keys so a second run replays instead of duplicating."""
    scope, turn_id = f"/u/{user_id}/", f"seed-layla-{position}"
    source = {"type": "external", "ref": f"app://session/{turn_id}", "span": text[:64],
              "content_hash": canonical_hash(text), "role": "user"}
    episode = {"scope": scope, "content": text, "source": source, "event_time": None,
               "idempotency_key": f"episode:{turn_id}"}
    await memory_inspector.call(user_id, "ingest_episode", episode)
    classification = SlotClassification("single", slot) if slot else MULTI
    arguments = _claim_arguments(scope, text, turn_id, classification)
    if mode is not None:
        arguments["assertion_mode"] = mode
    result = await memory_inspector.call(user_id, "assert", arguments)
    return str(result.get("effect") or result.get("reason_code"))
```

### Files

```text
tools/demo/
├── seed.py       # NEW
├── reset.py      # NEW
└── README.md     # MODIFIED
```

### Verification

```bash
uv run python tools/demo/up.py
uv run python tools/demo/seed.py && uv run python tools/demo/seed.py   # same counts twice
uv run python tools/demo/reset.py && uv run python tools/demo/seed.py
make check
```

### Dependencies

- Depends on: APP-01, APP-02, APP-05.
- Blocks: WEB-08, APP-09.
- External: none.

---

## [APP-07] · OpenAPI export and staleness test

**Label:** app, feature, test
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-03
**Blocks:** WEB-02

### Objective

Add `scripts/export_openapi.py`, which writes the app's OpenAPI document to `apps/api/docs/api/openapi.json`, and a test that
fails when the committed document differs from the app. The web app generates its TypeScript types from this file
(WEB-02), so a router change that is not exported fails CI instead of breaking the interface.

### Background and Current State

- `app.main:app` is a FastAPI instance, and `app.openapi()` builds the schema without running the lifespan (no
  database, no LLM).
- Settings are read at import time. `apps/api/tests/conftest.py` sets `APP_ENV=test`, a random `JWT_SECRET_KEY` and a dummy
  `OPENAI_API_KEY` before any `app` import. The script sets the same defaults.
- Every memory route declares `response_model`, so the document includes the memory schemas.

### Acceptance Criteria

- `uv run python scripts/export_openapi.py` writes `apps/api/docs/api/openapi.json` with sorted keys, two-space indent and a
  trailing newline, and needs no database, LLM key or `.env` file.
- `uv run python scripts/export_openapi.py --check` exits 0 when the file is current and 1 when it differs.
- `apps/api/tests/test_openapi_export.py` fails when a field is added to `MemoryItem` without re-exporting. The PR shows this
  once and then reverts it.
- The exported document contains `MemoryView`, `MemoryItem`, `ProvenanceOut`, `TimelineView`, `ChangesView`,
  `AdjudicateResult` and `ForgetResult` when APP-04 and APP-05 are merged. The test asserts the ones present at merge
  time.
- `make check` runs the staleness test.

### Implementation

```python
"""Write the app's OpenAPI document to apps/api/docs/api/openapi.json, the single source of the web app's API types."""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "api" / "openapi.json"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")
os.environ.setdefault("MEMORY_SLOT_CLASSIFIER", "off")
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402  (settings must see the environment above first)


def render() -> str:
    """The document exactly as committed: sorted keys, two-space indent, trailing newline."""
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return 0 if TARGET.exists() and TARGET.read_text(encoding="utf-8") == render() else 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

### Files

```text
scripts/export_openapi.py       # NEW
apps/api/docs/api/openapi.json           # NEW (generated, committed)
apps/api/tests/test_openapi_export.py    # NEW
```

### Verification

```bash
uv run python scripts/export_openapi.py --check
cd apps/api && uv run pytest -q apps/api/tests/test_openapi_export.py
make check
```

### Dependencies

- Depends on: APP-03 (re-run after APP-04 and APP-05).
- Blocks: WEB-02.
- External: none.

---

## [APP-08] · Checkpoint A report: journey J1 from a clean checkout

**Label:** app, test
**Plan task:** none
**Wave / Gate:** none; internal Checkpoint A of the Epic
**Issue:** Refs #TBD
**Depends on:** APP-01, WEB-05
**Blocks:** WEB-06, WEB-09, WEB-10, WEB-11

### Objective

A person who wrote none of APP-01, WEB-03 or WEB-05 runs journey J1 from a clean checkout, following only
`tools/demo/README.md` and `apps/studio/README.md`, and commits the result as `apps/api/docs/demo/checkpoint-a.md`. Phase 2 starts
only on an all-PASS report.

### Background and Current State

- J1: register, "I live in Berlin", "I moved to Paris last week", "Where do I live?", then the Arabic pair on a
  second user.
- The memory panel polls for up to 15 seconds after each reply (WEB-05).
- `apps/api/docs/demo/` does not exist.

### Acceptance Criteria

- The report records the commit SHA tested, the operating system, and the start and end time of the run.
- Every step has PASS or FAIL and a screenshot under `apps/api/docs/demo/checkpoint-a/`.
- Cold start to the first chat answer takes under 5 minutes, and the measured time is in the report.
- After the Paris message, the panel shows exactly one known fact (Paris) and both sentences under Conversation
  record.
- With the engine host stopped, chat still answers and the health indicator turns red. The report shows both.
- `npm run check` (in `apps/studio/`) and `make check` pass on the tested commit.
- Each README gap found is a linked follow-up issue. The report lists the issue numbers.
- Every step is PASS. A FAIL keeps this ticket open until a re-run passes.

### Implementation

```markdown
# Checkpoint A — journey J1 from a clean checkout

| Field | Value |
|---|---|
| Commit | <sha> |
| Operating system | <os and version> |
| Run by | <name> (wrote none of APP-01, WEB-03, WEB-05) |
| Cold start to first answer | <mm:ss> |

| # | Step | Result | Evidence | Notes |
|---|---|---|---|---|
| 1 | `tools/demo/up.py` all healthy | PASS / FAIL | checkpoint-a/01-up.png | |
| 2 | Register a new user, land on /chat | PASS / FAIL | checkpoint-a/02-register.png | |
| 3 | "I live in Berlin": Berlin under Known facts | PASS / FAIL | checkpoint-a/03-berlin.png | |
| 4 | "I moved to Paris last week": only Paris under Known facts | PASS / FAIL | checkpoint-a/04-paris.png | |
| 5 | "Where do I live?": the answer names Paris | PASS / FAIL | checkpoint-a/05-answer.png | |
| 6 | Arabic pair on a second user: one known fact | PASS / FAIL | checkpoint-a/06-arabic.png | |
| 7 | Engine host stopped: chat answers, indicator red | PASS / FAIL | checkpoint-a/07-engine-down.png | |
| 8 | `npm run check` and `make check` | PASS / FAIL | output pasted below | |

Follow-up issues: #<n>, #<n>
```

### Files

```text
apps/api/docs/demo/
├── checkpoint-a.md     # NEW
└── checkpoint-a/       # NEW (screenshots)
```

### Verification

```bash
uv run python tools/demo/up.py
cd apps/studio && npm ci && npm run check && npm run dev
make check
```

### Dependencies

- Depends on: APP-01, WEB-05.
- Blocks: WEB-06, WEB-09, WEB-10, WEB-11.
- External: an LLM provider key.

---

## [APP-09] · Checkpoint B report: journeys J1–J5 live in one run

**Label:** app, test
**Plan task:** none
**Wave / Gate:** none; internal Checkpoint B of the Epic
**Issue:** Refs #TBD
**Depends on:** APP-06, WEB-06, WEB-07, WEB-08, WEB-09, WEB-10, WEB-11
**Blocks:** WEB-12, WEB-13, WEB-14

### Objective

Two people, one driving and one recording, run the full demo script live, from a wiped stack, in one uninterrupted
session, and commit the result as `apps/api/docs/demo/checkpoint-b.md`. Phase 3 starts only on an all-PASS report.

### Background and Current State

- The stack is reset with `down.py --wipe`, `up.py` and `seed.py`.
- The journeys are J1 (remember and update), J2 (drawer), J3 (Time Travel), J4 (Forget, then a new session asks
  again), J5 (Layla, confirm the conflict), then the Arabic pair. The Evidence page opens and closes the run.

### Acceptance Criteria

- The report records the commit SHA, the operating system and the total duration.
- J1–J5 and the Arabic pair are each PASS, with a screenshot under `apps/api/docs/demo/checkpoint-b/`.
- The full run takes under 10 minutes.
- J4's four after-checks are all observed true, and the report shows the receipt counts.
- J5's conflict moves from Conflicts to Known facts after Confirm.
- Each FAIL becomes a linked bug issue. After the fix, the full run repeats, not only the failed step.
- `npm run check` and `make check` pass on the tested commit.

### Implementation

```markdown
# Checkpoint B — journeys J1 to J5 live

| Field | Value |
|---|---|
| Commit | <sha> |
| Operating system | <os and version> |
| Driver / recorder | <name> / <name> |
| Total duration | <mm:ss> |

| Journey | Result | Evidence | Duration | Notes |
|---|---|---|---|---|
| Evidence page opens | PASS / FAIL | checkpoint-b/00-evidence.png | | |
| J1 remember and update | PASS / FAIL | checkpoint-b/j1.png | | |
| J2 why do you believe that | PASS / FAIL | checkpoint-b/j2.png | | |
| J3 what did you believe before | PASS / FAIL | checkpoint-b/j3.png | | |
| J4 forget me (counts, three checks, new session) | PASS / FAIL | checkpoint-b/j4.png | | |
| J5 Layla confirms the conflict | PASS / FAIL | checkpoint-b/j5.png | | |
| Arabic pair | PASS / FAIL | checkpoint-b/arabic.png | | |

Linked bugs: #<n>
```

### Files

```text
apps/api/docs/demo/
├── checkpoint-b.md     # NEW
└── checkpoint-b/       # NEW (screenshots)
```

### Verification

```bash
uv run python tools/demo/down.py --wipe && uv run python tools/demo/up.py && uv run python tools/demo/seed.py
cd apps/studio && npm run check
make check
```

### Dependencies

- Depends on: APP-06, WEB-06, WEB-07, WEB-08, WEB-09, WEB-10, WEB-11.
- Blocks: WEB-12, WEB-13, WEB-14.
- External: an LLM provider key.

---

## [APP-10] · Demo readiness report and two timed rehearsals

**Label:** app, docs
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-12, WEB-13, WEB-14
**Blocks:** none

### Objective

Commit `apps/api/docs/demo/readiness.md`: the demo-laptop checklist, ticked on the laptop itself, and the record of two timed
rehearsals, one of them with a forced switch to mock mode. This closes the Epic's demo-length and fallback metrics.

### Background and Current State

- The live spec (`npm run e2e:live`, WEB-12), the mock build (`npm run build:mock`, WEB-13) and the failure drills
  (WEB-14) exist.
- The LLM key and its quota are the only external dependency of the live run.

### Acceptance Criteria

- Every checklist line is ticked, with the date it was checked, on the demo laptop:
  - Docker images pulled, `uv sync` done for root and engine, `npm ci` done;
  - `up.py` cold start under 3 minutes, `check.py` PASS, `seed.py` done;
  - `npm run e2e:live` green on that day;
  - the mock build served in a second tab;
  - LLM key and quota checked;
  - zoom between 110 and 125 percent, notifications off.
- Two rehearsals are recorded with date, duration and problems found. Both are under 9 minutes.
- One rehearsal includes a forced switch to the mock build, measured under 30 seconds.
- One rehearsal includes the LLM-down path (seeded Layla only).
- Every problem found has a linked fix or a written acceptance decision.
- The report names where the screen recording of rehearsal 2 is stored. The recording itself is not committed.

### Implementation

```markdown
# Demo readiness

## Laptop checklist
| Item | Checked on | By |
|---|---|---|
| Docker images pulled; `uv sync` root and engine; `npm ci` | | |
| `up.py` cold start under 3 min; `check.py` PASS; `seed.py` done | | |
| `npm run e2e:live` green today | | |
| Mock build served in a second tab | | |
| LLM key valid and quota checked | | |
| Zoom 110–125 %, notifications off, other tabs closed | | |

## Rehearsals
| # | Date | Duration | Forced fallback | Switch time | Problems found | Fixed in |
|---|---|---|---|---|---|---|
| 1 | | | mock build | | | |
| 2 | | | LLM down, Layla only | | | |

Recording of rehearsal 2 stored at: <location>
```

### Files

```text
apps/api/docs/demo/readiness.md   # NEW
```

### Verification

```bash
uv run python tools/demo/up.py && uv run python tools/demo/check.py
cd apps/studio && npm run e2e:live
```

### Dependencies

- Depends on: WEB-12, WEB-13, WEB-14.
- Blocks: none.
- External: an LLM provider key.

---

## Settled by the first bundle's live tests

`apps/api/tests/integration/test_memory_router_live.py` observed each of these against a real engine host:

- `query` with `mode: current_state` and no `text` returns every current item up to `limit` (APP-03).
- For the Berlin → Paris move, `mode: change` reports `changed` for the residence and `added` for the new turn (APP-04).
- `export.artifact_count` is 0 right after `delete` scope `all` (APP-05).
- `MAX_PROVENANCE_DEPTH` is 8 (`engine/provenance.py`); the router bounds `depth` to 1–8 (APP-03).
- `assert` with `assertion_mode: "hedged"` is accepted and held as `contested`, but `query` delivers a held claim in
  **no** mode until it is adjudicated (`API_V2.md` §4.2). `get` by the receipt's id returns it, in the `conflicts`
  channel. APP-06 and WEB-08 depend on this.

Implementation notes from the same bundle, which extend the tickets above:

- `MemoryItem` also carries `bound` (`I14`: an abstraction never renders without it) and `conflict_sides`.
- `GET /{id}` answers `MemoryItemResponse` (the item plus `request_id`).
- `ForgetChecks` values are nullable: `null` means "could not be checked after the erasure committed", never "passed".
- Forget also reports `failed_chat_sessions` and the check `chat_history_cleared` (review finding on #307). A
  transcript that could not be cleared makes the forget incomplete; calling `POST /forget` again retries it.
- An engine answer outside the contract is HTTP 502 `engine_response_invalid`.

## Needs confirmation

- **Owner decision for J5 (APP-06, WEB-08):** a held claim is not listed by any `query`, so the Conflicts tab cannot
  discover it. The options are:
  - the seed prints the held id, and the demo opens `/memory/<id>` directly;
  - the app keeps the ids of held claims it wrote;
  - an engine-side listing of pending claims, which would need its own decision record.
