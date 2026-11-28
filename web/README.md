# ChronaMind Studio (`web/`)

Frontend scaffold for ChronaMind Studio — a Vite, React 19, TypeScript (strict), Tailwind CSS v4, and shadcn/ui web application that talks to the FastAPI backend through a same-origin proxy.

## Prerequisites

- **Node.js 22 LTS** (`22.22.2+` required by the pinned toolchain; major version pinned in `.nvmrc` and `package.json#engines`: `>=22 <23`).
- **Backend application** listening on `http://127.0.0.1:8000`:
  - Full demo stack once APP-01 (`#277`) is merged: `uv run python scripts/demo/up.py`
  - App-only development server from the repository root (available now): `make dev` (`uv run python run.py`)

## Install

```bash
cd web
npm ci
```

## Development and Preview

Start the Vite dev server on port `5173`:

```bash
cd web
npm run dev
```

Build and preview the production bundle on port `4173`:

```bash
cd web
npm run build
npm run preview
```

Both `npm run dev` and `npm run preview` proxy `/api` and `/health` to `http://127.0.0.1:8000` (`changeOrigin: false`), so the browser uses a single origin and no CORS configuration is needed. With the backend running, `fetch("/health")` from the page returns HTTP 200.

## API types and client

The web app never hand-writes an API type. `src/api/schema.d.ts` is generated from the app's OpenAPI document
(`docs/api/openapi.json`, itself exported by `scripts/export_openapi.py`):

```bash
cd web
npm run gen:api
```

Run it whenever the backend's routes or response models change, and commit the result. `src/api/types.ts` only gives
the generated shapes short names.

Only `src/api/` talks to the server (an ESLint rule rejects `fetch` anywhere else). Use its typed functions:

| File        | Routes                                                                 | Token                   |
| ----------- | ---------------------------------------------------------------------- | ----------------------- |
| `auth.ts`   | register (JSON), login (**form** fields), create chat session          | none / user             |
| `chat.ts`   | chat history and send (only the new message; the server keeps history) | **session**             |
| `memory.ts` | the memory inspector (`docs/memory.md`)                                | **user** (health: none) |

`client.ts` sends the right token per call and turns every error into an `ApiError` with a `code`. It handles all
three error shapes the server uses. `errors.ts` (`messageFor`) turns any error into one plain sentence. A 401 on a
signed-in call ends the session; a 401 from the login form ("Incorrect email or password") is a form error.

## Signing in and the chat page

- **Sign in** (`/login`): Register or Sign in, then the app opens a chat session at once. The session (user token +
  chat-session token) lives in `src/auth/sessionStore.ts`, outside React, with a `sessionStorage` copy, so a reload
  stays signed in and the client has the right token from the very first request. A 401 on a signed-in call ends the
  session and the login page says "Session expired"; a wrong password is only a form error.
- **Protected pages** (`RequireAuth`): chat, memory, time travel and forget. Login, Evidence and the start page are
  public. Tests that prepare `sessionStorage` call `sessionStore.reload()` to simulate a page load.
- **Chat** (`/chat`): the conversation on the left, the memory panel on the right. A send carries only the new message.
  After each reply the panel refreshes every 1.5 s until the memory has changed and held still for two refreshes, or
  for at most 15 s (`src/features/chat/memoryPolling.ts`), because the app writes memory a few seconds _after_ it
  answers. The send time of each message is kept for Time Travel (`turnMarkers.ts`).

## Quality Gates

Run every gate in order (`tsc -b --noEmit`, `eslint .`, `prettier --check .`, `vitest run`):

```bash
cd web
npm run check
```

Individual commands:

- `npm run typecheck` — TypeScript project build check (`tsc -b --noEmit`)
- `npm run lint` — ESLint across the workspace (`eslint .`)
- `npm run format:check` / `npm run format` — Prettier formatting check / write
- `npm run test` — Vitest unit tests (`vitest run`)

## Directory Layout

Scaffolded directories (initially empty `.gitkeep` placeholders populated by follow-up tickets):

- `src/api/` — generated OpenAPI types (`schema.d.ts`), the typed client and one module per route group (see above)
- `src/auth/` — Reserved for authentication context and route guards (WEB-03)
- `src/components/` — Reserved for shared UI components and shadcn/ui primitives (WEB-04)
- `src/features/` — Reserved for feature modules
- `src/pages/` — Reserved for top-level route views
- `src/content/` — Reserved for static copy and evidence content
- `src/mocks/` — Reserved for mock data and handlers for recorded demo mode

Active scaffold directories:

- `src/lib/` — Shared helpers (`cn` class-name utility for shadcn/ui)
- `src/test/` — Vitest setup
