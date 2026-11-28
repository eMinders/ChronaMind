# ChronaMind Studio (`apps/studio/`)

Frontend scaffold for ChronaMind Studio — a Vite, React 19, TypeScript (strict), Tailwind CSS v4, and shadcn/ui web application that talks to the FastAPI backend through a same-origin proxy.

## Prerequisites

- **Node.js 22 LTS** (`22.22.2+` required by the pinned toolchain; major version pinned in `.nvmrc` and `package.json#engines`: `>=22 <23`).
- **Backend application** listening on `http://127.0.0.1:8000`:
  - Full demo stack once APP-01 (`#277`) is merged: `uv run --project apps/api python tools/demo/up.py`
  - App-only development server from the repository root (available now): `make dev` (`cd apps/api && uv run python run.py`)

## Install

```bash
cd apps/studio
npm ci
```

## Development and Preview

Start the Vite dev server on port `5173`:

```bash
cd apps/studio
npm run dev
```

Build and preview the production bundle on port `4173`:

```bash
cd apps/studio
npm run build
npm run preview
```

Both `npm run dev` and `npm run preview` proxy `/api` and `/health` to `http://127.0.0.1:8000` (`changeOrigin: false`), so the browser uses a single origin and no CORS configuration is needed. With the backend running, `fetch("/health")` from the page returns HTTP 200.

## Quality Gates

Run every gate in order (`tsc -b --noEmit`, `eslint .`, `prettier --check .`, `vitest run`):

```bash
cd apps/studio
npm run check
```

Individual commands:

- `npm run typecheck` — TypeScript project build check (`tsc -b --noEmit`)
- `npm run lint` — ESLint across the workspace (`eslint .`)
- `npm run format:check` / `npm run format` — Prettier formatting check / write
- `npm run test` — Vitest unit tests (`vitest run`)

## Directory Layout

Scaffolded directories (initially empty `.gitkeep` placeholders populated by follow-up tickets):

- `src/api/` — Reserved for generated OpenAPI schema types and HTTP client (WEB-02)
- `src/auth/` — Reserved for authentication context and route guards (WEB-03)
- `src/components/` — Reserved for shared UI components and shadcn/ui primitives (WEB-04)
- `src/features/` — Reserved for feature modules
- `src/pages/` — Reserved for top-level route views
- `src/content/` — Reserved for static copy and evidence content
- `src/mocks/` — Reserved for mock data and handlers for recorded demo mode

Active scaffold directories:

- `src/lib/` — Shared helpers (`cn` class-name utility for shadcn/ui)
- `src/test/` — Vitest setup
