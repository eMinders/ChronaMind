# ChronaMind Studio (`web/`)

Frontend scaffold for ChronaMind Studio — a Vite, React 19, TypeScript (strict), Tailwind CSS v4, and shadcn/ui web application that talks to the FastAPI backend through a same-origin proxy.

## Prerequisites

- **Node.js 22 LTS** (pinned in `.nvmrc` and `package.json#engines`: `>=22 <23`).
- **Backend application** listening on `http://127.0.0.1:8000`:
  - Full demo stack (APP-01): `uv run python scripts/demo/up.py`
  - Or app-only development server from the repository root: `make dev` (`uv run python run.py`)

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

- `src/api/` — Generated OpenAPI schema types and HTTP client
- `src/auth/` — Authentication context and route guards
- `src/components/` — Shared UI components and shadcn/ui primitives
- `src/features/` — Feature modules
- `src/pages/` — Top-level route views
- `src/content/` — Static copy and evidence content
- `src/mocks/` — Mock data and handlers for recorded demo mode
- `src/lib/` — Shared helpers (`cn` class-name utility)
- `src/test/` — Vitest setup
