# [Epic] ChronaMind Studio — demo interface for the agent and its memory

## Objective

An end user signs in to a new web app, ChronaMind Studio (`apps/studio/`), chats with the existing FastAPI + LangGraph agent,
and sees beside the chat what the memory engine holds about them: known facts separated from hypotheses, procedures,
conflicts and the verbatim conversation record. They can also see where each memory came from, what the engine
believed at an earlier instant, and a content-free receipt proving that "forget me" erased everything. The browser
calls only the app. The app gains one authenticated router, `/api/v1/memory/*`, which acts as the signed-in user
through a Tier C delegating token and calls the engine operations `query`, `get`, `provenance`, `adjudicate`,
`delete` and `export` over `POST /v1/call`. The memory engine (`core/memory-engine/`) is not changed.

## Background and Current State

**Existing files the Epic relies on and does not change in behaviour**

- `apps/api/app/services/memory_engine.py`: `MemoryEngineClient` with the agent's three calls (`initialize`, `search`, `add`);
  `add` runs `ingest_episode`, then `assert` with a slot classification, in a background task after each reply.
- `apps/api/app/core/langgraph/graph.py`: `LangGraphAgent.get_response` runs `memory_engine.search` concurrently with the state
  check, and schedules `memory_engine.add` with `asyncio.create_task` after the reply.
- `apps/api/app/api/v1/auth.py`: `get_current_user` (user token, `sub` = user id) and `get_current_session` (session token).
- `apps/api/app/api/v1/chatbot.py`: the module-level `agent = LangGraphAgent()` and the existing chat routes.
- `apps/api/app/services/database.py`: `get_user_sessions(user_id)` and `advance_history_epoch(session_id)`.
- `core/memory-engine/src/custom_memory_engine/host/credentials.py`: delegating tokens keep their tier and are never
  admin (`V2-D120`).
- `apps/api/tests/integration/test_app_engine_boundary.py`: the proven recipe for running a real engine host for the app
  (migrate, provision the role, `core/memory-engine/scripts/run_host.py`).

**Engine operations used** (all Implemented and wired; contract in `core/memory-engine/src/custom_memory_engine/api/contract.py`)

| Operation | Tier | Defined in |
|---|---|---|
| `query` (modes `current_state`, `audit_as_of`, `change`) | B, C (`audit_as_of` Tier C only) | `PLAN/V2/API_V2.md` §4.2 |
| `get`, `provenance` | B, C | `API_V2.md` §4.2 |
| `ingest_episode` | B, C | `API_V2.md` §4.1 |
| `assert` | B, C | `API_V2.md` §4.3 |
| `adjudicate` | C | `API_V2.md` §4.6 |
| `delete` | C | `API_V2.md` §4.8 |
| `export` | C | `API_V2.md` §4.9 |

**App HTTP routes used** (all Implemented)

| Method and path | Purpose |
|---|---|
| `POST /api/v1/auth/register` | JSON `{email, password, username?}`; returns `id`, `email`, `username`, `token.access_token` |
| `POST /api/v1/auth/login` | Form fields `email`, `password`, `grant_type=password`; returns `access_token` <!-- pragma: allowlist secret --> |
| `POST /api/v1/auth/session` | User token; returns `session_id` and a session `token.access_token` |
| `GET /api/v1/auth/sessions` | User token; lists the user's sessions |
| `POST /api/v1/chatbot/chat` | Session token; body carries only the new message; returns the session's message list |
| `GET /api/v1/chatbot/messages`, `DELETE /api/v1/chatbot/messages` | Session token; read or clear one session's history |
| `GET /health` | App health |

**Tables involved.** The Epic reads and writes engine tables only through the operations above, and adds no
migration. App tables used unchanged: `user`, `session` (including `history_epoch`), and the LangGraph checkpoint
tables, which LangGraph manages, not Alembic.

**The gap.** No screen shows any of the engine's guarantees. The app exposes chat but no memory, and the agent's
token is Tier B, which cannot `delete`, `adjudicate`, `export` or run `audit_as_of`.

**Known limitations this Epic does not address**

- Per-item deletion: `delete` scope `memory` takes a `memory_key` that no API response exposes, so this Epic deletes
  with scope `all`.
- Artifacts carry no timestamps and provenance carries no source text, so the interface shows neither.
- Restore-recovery limitations are tracked in #261–#264.

**Status of capabilities named here.** Implemented: the 32 operations and the HTTP host, delegating tokens, the
app's slot classifier. Planned: the cognition runtime that proposes hypotheses (W5), the Personal Digital Twin
application (W7). Hypotheses therefore appear in the interface only when seeded.

## Plan Alignment

- **Wave and gate:** outside the V2 roadmap waves. This is demonstration support built on the W3.5 substrate (gate
  G3.5 met on 2026-11-26) and serves no gate criterion. It does not deliver W7's Personal Digital Twin application.
- **Roadmap task IDs:** none.
- **Decisions relied on:**
  - `V2-D93`: the app reaches the engine over HTTP, never by import.
  - `V2-D120`: a delegating token acts for the end user it names and keeps its tier.
  - `V2-D47`: two tiers, with admin a role within Tier C.
  - `V2-D73`: the reference instant is the engine clock.
  - `V2-D81`: `change` diffs two views.
  - `V2-D86`: `audit_as_of` and `change` take no text.
  - `V2-D87`: held writes are `contested`.
  - `V2-D99`: an export is a manifest, not a copy.
  - `V2-D116`: no plaintext memory cache in the app.
  - `V2-D121`: the slot classifier.
  - `V2-D37`: opaque provenance handles.

  The app holding a second delegating token at Tier C is an application of `V2-D120`. Whether it needs its own
  decision record is listed under Dependencies as an open owner decision.
- **Invariants touched:**
  - `I4` (belief time is the database clock): no request carries `reference_time` or a caller belief clock beyond the
    `audit_as_of` instant the engine validates.
  - `I5` (no bi-temporal overlap): observed only, through single-valued supersession.
  - `I8` (deletion reaches derived artifacts): exercised by `delete` scope `all`.
  - `I14` (channel separation): the interface renders the engine's channels without merging them.
  - `I17` (opaque provenance): opaque references are shown without content.

  All stay ENFORCED, because no engine code changes.
- **Threats:** `T-42` (a service token acts for any end user) applies to the new Tier C inspector token. Whoever holds
  it can also delete and export for every end user. `T-43` (a client keeps a copy of rendered memory) is respected,
  because the router caches nothing. No `THREAT_MODEL.md` row changes in this Epic. Whether `T-42` should name the
  inspector token is listed under Dependencies.

## Acceptance Criteria

### Engine

No changes. `git diff main -- core/memory-engine/` is empty for every PR in this Epic.

### App

- `GET /api/v1/memory/` returns the signed-in user's memory split into `known_facts`, `hypotheses`, `procedures`,
  `conflicts` and `conversation`. No item of kind `episode` appears outside `conversation`.
- After the agent records "I live in Berlin" and then "I moved to Paris", `known_facts` holds exactly one residence
  claim, containing "Paris".
- `GET /api/v1/memory/timeline?at=T` for an instant between the two writes returns the Berlin claim and not the Paris
  claim.
- `GET /api/v1/memory/{id}` for an id owned by another user returns HTTP 404 with code `forbidden`.
- `POST /api/v1/memory/{id}/adjudicate` with `confirm` on a `contested` claim moves it to `known_facts`. A second call
  returns HTTP 409 with code `precondition_failed`.
- `POST /api/v1/memory/forget` deletes with scope `all`, clears every chat session of that user, and returns counts
  only. Afterwards `memory_engine.search` for that user returns an empty string.
- The agent path still sends `MEMORY_ENGINE_TOKEN`, never `MEMORY_ENGINE_USER_TOKEN`.
- Every router route has live integration tests against a real engine host, and `make check` passes.

### Web

- A new user registers, chats, and sees the residence fact change from Berlin to Paris in the memory panel within
  15 seconds of the reply, with both sentences listed under Conversation record.
- The detail drawer shows kind, lifecycle, channel and provenance references, and shows no timestamp or quote field.
- Time Travel shows Berlin as believed at the instant of the Paris message and Paris as believed now.
- Forget shows the deletion counts and four observed after-checks: chat history cleared, memory list empty, export
  count 0, a prior id not found. A failed check is shown as failed, never as "forgotten".
- The seeded user's contested claim moves to Known facts after Confirm.
- The Playwright demo spec passes three consecutive runs against the live stack, and the mock-mode spec passes in CI.

## High-Level Requirements and Scope

### In Scope

| # | Requirement |
|---|---|
| 1 | A one-command local stack: engine database, engine host with two delegating tokens, app database, app |
| 2 | An app setting `MEMORY_ENGINE_USER_TOKEN` and an inspector client that raises typed engine errors |
| 3 | The `/api/v1/memory/*` router: health, list, search, get, provenance, timeline, changes, adjudicate, forget |
| 4 | Router response models as the single source of the generated TypeScript types |
| 5 | A seed and reset script for the demo user Layla (facts, a superseded value, a contested claim) |
| 6 | `apps/studio/`: sign in, chat with memory panel, Explorer, detail drawer, conflict resolution, Time Travel, Forget, Evidence page |
| 7 | A CI job for `apps/studio/` (checks, type staleness, mock end-to-end), and the router's live tests added to the required `app-engine-boundary` job |
| 8 | Live end-to-end tests, a mock mode built from recorded responses, failure drills, two checkpoints and rehearsals |

### Out of Scope

- Per-item deletion, timestamps in artifacts and quote spans in provenance. Each needs an engine change and a
  decision record.
- The cognition runtime that proposes hypotheses (W5), the user model, and the Personal Digital Twin application
  (W7).
- Scopes and sharing, procedures, admin maintenance operations, Grafana dashboards.
- Returning "what the model saw this turn" in the chat response.
- Hosting, mobile layouts, internationalisation beyond `dir="auto"` for Arabic text.

## Implementation Steps (Phases)

1. **Phase 0, live backend and contract (app, then ci).** Add the demo stack scripts. Add the inspector token and
   the typed engine call to the app service layer. Add the memory router in three PRs (read; time; write), each with
   unit tests against a fake transport and live tests against a real host. Then the seed and reset scripts, and the
   OpenAPI export with its staleness test.
2. **Phase 1, walking skeleton (web, ci).** Scaffold `apps/studio/` and its CI job. Generate the API types and the client.
   Build sign-in, the shell and shared components, then chat with the polling memory panel. The phase ends with
   Checkpoint A: a second person runs journey J1 from a clean checkout and commits the report.
3. **Phase 2, screens (web).** Explorer, drawer, conflict resolution, Time Travel, Forget, Evidence page. The phase
   ends with Checkpoint B: journeys J1–J5 run live in one session, and the report is committed.
4. **Phase 3, hardening (web, ci, app).** The Playwright demo spec against the live stack. Mock mode from recorded
   responses, with its CI run. Failure drills. Two timed rehearsals with the report committed.

## Files Modified and Created

```text
ChronaMind/
├── CLAUDE.md                                   [MODIFIED]
├── .env.example                                [MODIFIED]
├── .github/workflows/ci.yaml                   [MODIFIED] router live tests in app-engine-boundary
├── .github/workflows/web.yaml                  [NEW]
├── apps/api/app/
│   ├── api/v1/api.py                           [MODIFIED]
│   ├── api/v1/memory.py                        [NEW]
│   ├── core/config.py                          [MODIFIED]
│   ├── main.py                                 [MODIFIED]
│   ├── schemas/memory.py                       [NEW]
│   ├── services/memory_engine.py               [MODIFIED]
│   └── services/memory_view.py                 [NEW]
├── apps/api/docs/
│   ├── memory.md                               [MODIFIED]
│   ├── api/openapi.json                        [NEW] generated, committed
│   └── demo/                                   [NEW] checkpoint and rehearsal reports
├── scripts/
│   ├── export_openapi.py                       [NEW]
│   └── demo/                                   [NEW] up.py, down.py, check.py, seed.py, reset.py, README.md, .gitignore
├── apps/api/tests/
│   ├── test_memory_inspector_client.py         [NEW]
│   ├── test_memory_router.py                   [NEW]
│   ├── test_memory_view.py                     [NEW]
│   ├── test_openapi_export.py                  [NEW]
│   └── integration/test_memory_router_live.py  [NEW]
└── apps/studio/                                        [NEW] the whole folder
```

```diff
# .env.example (placeholders only)
+MEMORY_ENGINE_USER_TOKEN=<tier-c-delegating-token>
+RATE_LIMIT_MEMORY=60 per minute

# engine host CME_HOST_TOKENS (generated into the untracked tools/demo/.env.demo)
+{"<agent-token>": {"principal": "chronamind-app", "tier": "B", "delegate": true},
+ "<inspector-token>": {"principal": "chronamind-inspector", "tier": "C", "delegate": true}}

# apps/studio/package.json (new): react, react-dom, react-router, @tanstack/react-query, tailwindcss,
# openapi-typescript, msw, vitest, @testing-library/react, @playwright/test
```

## Technical Details and Dependencies

| # | Detail |
|---|---|
| 1 | Tier: the router uses a Tier C delegating token because `adjudicate`, `delete`, `export` and `audit_as_of` refuse Tier B (`contract.py` rules TB1, TB2, TB9; `read_handlers.py` refuses `audit_as_of` for Tier B). The agent keeps its Tier B token |
| 2 | Principal: always `str(user.id)` from `get_current_user`, the same spelling the agent uses (`str(session.user_id)`). No request field names a principal or a tier |
| 3 | `export` returns `snapshot_id`, `captured_epoch`, `artifact_count`, `manifest_digest`, `scope_paths`, `captured_at`, and no item. Listing therefore uses `query` with `mode: current_state`, no `text`, `limit` up to 1000 |
| 4 | An artifact over the API carries `artifact_id`, `kind`, `lifecycle`, `channel`, `content`, `verification`, `label`, `support_summary`, `bound`, `conflict_sides`, `provenance`, `scope_path`, `hop_distance`, `preconditions`, `steps`. There are no timestamps |
| 5 | A provenance reference is either readable (`source_id`, `scope_path`, `source_type`, `derivation_type`) or opaque (`opaque_ref`, `derivation_type`). No source text is returned. The router never forwards `scope_path` |
| 6 | The `facts` channel contains episodes (verbatim turns of both speakers, never superseded). The router splits them into `conversation`, as `memory_engine._render` does |
| 7 | `audit_as_of` requires `target_time` and `belief_time` and refuses `text`. `change` requires `window_start` and `window_end` and refuses `text`. A request with `reference_time` is refused (`I4`). The router sends `target_time = belief_time = at` |
| 8 | `delete` takes `scope` in `memory`, `topic`, `all`; `memory` matches `memory_key`, `topic` matches `memory_key` or `subject_key`. The demo uses `all` |
| 9 | The chat transcript lives in the app's LangGraph checkpointer, and the graph state uses the `add_messages` reducer. Forget therefore also calls `agent.clear_chat_history` and `advance_history_epoch` for every session of the user |
| 10 | Memory is written after the reply in a background task, after one LLM call for slot classification. The panel polls for up to 15 seconds. The engine worker is not on this path |
| 11 | `assert` with `assertion_mode` `hedged` or `hypothetical` is held: the receipt says `contested`, and `get` returns it in the `conflicts` channel. `query` delivers a held claim in **no** mode until it is adjudicated (`API_V2.md` §4.2); only the receipt's id reaches it (observed in the first bundle's live tests). `adjudicate` accepts `confirm` (becomes `asserted`) or `deny` (becomes `retired`) |
| 12 | Host statuses: `validation_error` 400, `forbidden` 403, `precondition_failed` 412, `resource_limit` 429, `retryable_conflict` 409, `internal_error` 500. The router maps them to 422, 404, 409, 429, 409 after one retry, and 502, keeping the engine code in the body |
| 13 | `RATE_LIMIT_REGISTER` defaults to 10 per hour. Development and test runs raise it |
| 14 | On Windows with Avast, the app needs the workarounds in `.claude/skills/run-chronamind/SKILL.md` |
| 15 | The existing live-test fixture hardcodes `.venv/bin/python`. New live tests resolve the engine interpreter per platform |

## Success Metrics

| Metric | Target |
|---|---|
| Invariant ledger | All 17 invariants remain ENFORCED; `core/memory-engine/tests/test_invariants_declared_and_enforced.py` passes (no engine change) |
| Regression | `make check` passes and the engine suite is untouched; no test removed, skipped or loosened |
| Plan consistency | `check_plan_refs`, `audit_status --check` and `plan_coverage --check` pass unchanged |
| Gate criterion | None (demo support). The internal gates are Checkpoint A (J1 live from a clean checkout) and Checkpoint B (J1–J5 live in one run) |
| Live journeys | J1–J5 pass in the Playwright live spec three consecutive times on the demo laptop |
| Demo length | Two rehearsals under 9 minutes each; switching to mock mode takes under 30 seconds |
| Honesty | Every number on the Evidence page matches `SHOWCASE.md` §8.2 and carries its N and the label "development split" |

## Dependencies

- No engine prerequisite. Every operation used is Implemented and wired.
- An LLM provider key for the chat journeys. Journeys J2–J5 also run on the seeded user without it.
- Open owner decisions, not blocking Phase 0:
  - Whether the app's Tier C inspector token needs its own `V2-D` record, or an amendment to `T-42`. Either is an
    engine-side documentation ticket outside this Epic.
  - Whether the `apps/studio` CI job becomes a required check.

## References

| Source | Path or URL |
|---|---|
| Project showcase (content and claims) | `SHOWCASE.md` |
| Engine contract | `core/memory-engine/src/custom_memory_engine/api/contract.py` |
| Read path, query modes, artifact shape | `core/memory-engine/src/custom_memory_engine/api/read_handlers.py` |
| Deletion scopes and receipt | `core/memory-engine/src/custom_memory_engine/engine/deletion.py`, `api/delete_handlers.py` |
| Export manifest | `core/memory-engine/src/custom_memory_engine/engine/export.py` |
| Gate holds | `core/memory-engine/src/custom_memory_engine/engine/gate.py` |
| Host credentials and statuses | `core/memory-engine/src/custom_memory_engine/host/credentials.py`, `host/app.py` |
| API reference | `core/memory-engine/PLAN/V2/API_V2.md` §4.1–4.9 |
| Decisions | `core/memory-engine/PLAN/V2/DECISIONS_V2.md` (`V2-D37`, `V2-D47`, `V2-D73`, `V2-D81`, `V2-D86`, `V2-D87`, `V2-D93`, `V2-D99`, `V2-D116`, `V2-D120`, `V2-D121`) |
| Threats | `core/memory-engine/PLAN/V2/THREAT_MODEL.md` (`T-42`, `T-43`) |
| Invariants | `core/memory-engine/PLAN/V2/INVARIANT_LEDGER.md` (`I4`, `I5`, `I8`, `I14`, `I17`) |
| App memory client and boundary test | `apps/api/app/services/memory_engine.py`, `apps/api/tests/integration/test_app_engine_boundary.py` |
| App rules | `AGENTS.md`, `CLAUDE.md` |
| **Vite** | https://vite.dev |
| **React** | https://react.dev |
| **TanStack Query** | https://tanstack.com/query |
| **Tailwind CSS** and **shadcn/ui** | https://tailwindcss.com, https://ui.shadcn.com |
| **openapi-typescript** | https://openapi-ts.dev |
| **MSW** | https://mswjs.io |
| **Vitest** and **Playwright** | https://vitest.dev, https://playwright.dev |
