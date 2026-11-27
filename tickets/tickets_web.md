# Tickets — Epic: ChronaMind Studio demo interface — Label: web

Side: `web/**` (Node 22 LTS, TypeScript strict). Verify with `npm run check` inside `web/`. Until WEB-01 merges,
every file under `web/` is new.

## References

| Source | Path |
|---|---|
| Parent Epic | [`tickets/EPIC-demo-interface.md`](EPIC-demo-interface.md) |
| App tickets (the API these screens call) | [`tickets/tickets_app.md`](tickets_app.md) |
| Generated API document | `docs/api/openapi.json` (APP-07) |
| Content and claims for the Evidence page | [`SHOWCASE.md`](../SHOWCASE.md) §4, §8.2, §10, §13 |
| Channels and their meaning | [`custom-memory-engine/PLAN/V2/CHANNELS.md`](../custom-memory-engine/PLAN/V2/CHANNELS.md) |

Every live acceptance criterion below runs against the stack from APP-01 (`uv run python scripts/demo/up.py`), with
`npm run dev` on port 5173 proxying `/api` and `/health` to the app on port 8000.

---

## [WEB-01] · Scaffold the web app

**Label:** web, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** none
**Blocks:** WEB-02, WEB-04, CI-01

### Objective

Create `web/` as a Vite + React 19 + TypeScript (strict) app with Tailwind CSS, shadcn/ui, ESLint, Prettier and
Vitest. One `npm run check` script runs every gate. Dev and preview servers proxy `/api` and `/health` to the app, so
the browser talks to one origin and no CORS configuration is needed.

### Background and Current State

- `web/` does not exist. The app serves on `127.0.0.1:8000` (`run.py`, or APP-01).
- The app's `ALLOWED_ORIGINS` defaults to `*`. With a same-origin proxy, it stays unchanged.
- The root Ruff, Pyright and pytest configurations include `app`, `evals` and `tests` only, so `web/` is outside them.

### Acceptance Criteria

- `cd web && npm ci && npm run check` exits 0. `check` runs, in order: `tsc -b --noEmit`, `eslint .`,
  `prettier --check .` and `vitest run`.
- `npm run dev` serves on port 5173. With the app running, `fetch("/health")` from the page returns 200 with no CORS
  error in the console.
- `npm run preview` proxies `/api` and `/health` the same way.
- `web/.nvmrc` contains `22`, and `package.json` declares `"engines": {"node": ">=22 <23"}`.
- `web/.gitignore` excludes `node_modules/`, `dist/`, `dist-mock/`, `test-results/`, `playwright-report/`.
- These folders exist with an `index.ts` or `.gitkeep`: `src/api/`, `src/auth/`, `src/components/`,
  `src/features/`, `src/pages/`, `src/content/`, `src/mocks/`.
- One placeholder route renders, and one Vitest test passes.
- `web/README.md` lists the prerequisites, `npm ci`, `npm run dev` (with the APP-01 stack) and `npm run check`.
- `make check` at the repository root still passes.

### Implementation

```ts
// web/vite.config.ts
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const app = { target: "http://127.0.0.1:8000", changeOrigin: false };
const proxy = { "/api": app, "/health": app };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: 4173, strictPort: true, proxy },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
```

### Files

```text
web/
├── .gitignore            # NEW
├── .nvmrc                # NEW
├── README.md             # NEW
├── components.json       # NEW (shadcn/ui)
├── eslint.config.js      # NEW
├── index.html            # NEW
├── package.json          # NEW
├── package-lock.json     # NEW
├── tsconfig.json         # NEW
├── vite.config.ts        # NEW
└── src/
    ├── main.tsx          # NEW
    ├── App.tsx           # NEW
    ├── App.test.tsx      # NEW
    └── test/setup.ts     # NEW
```

### Verification

```bash
cd web && npm ci && npm run check
cd web && npm run dev     # with the app running: fetch("/health") returns 200
make check
```

### Dependencies

- Depends on: none.
- Blocks: WEB-02, WEB-04, CI-01.
- External: **vite**, **react@19**, **tailwindcss@4**, **shadcn/ui**, **vitest**, **@testing-library/react**,
  **eslint**, **prettier**. Exact versions are pinned in `package-lock.json`.

---

## [WEB-02] · Generated API types, typed client and error messages

**Label:** web, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-01, APP-07
**Blocks:** WEB-03, CI-03

### Objective

Generate `web/src/api/schema.d.ts` from `docs/api/openapi.json` with openapi-typescript, and add `src/api/client.ts`
with one `request<T>()` function. Every response type comes from the generated file, so a backend change that is not
followed here breaks the build, not the demo.

### Background and Current State

- APP-07 commits the app's OpenAPI document to `docs/api/openapi.json`.
- The app returns errors as FastAPI `HTTPException` bodies: `{"detail": {"code": <engine code>, "detail": <string or
  null>}}` for memory routes, and `{"detail": <string>}` for auth routes.
- Memory routes take the **user** token. Chat routes take the **session** token. Login and register take none.
- Status mapping from APP-03: `validation_error` 422, `forbidden` 404, `precondition_failed` 409, `resource_limit`
  429, `retryable_conflict` 409, `internal_error` and `engine_unavailable` 502, `inspector_not_configured` 503.

### Acceptance Criteria

- `npm run gen:api` runs `openapi-typescript ../docs/api/openapi.json -o src/api/schema.d.ts`. On a clean tree it
  produces no diff.
- `src/api/` contains no hand-written interface that duplicates a schema from `schema.d.ts` (review check).
- `request()` sends `Authorization: Bearer <token>` for the requested token kind (`user` or `session`), and no header
  for `none` (unit test).
- A 404 with `detail.code` `forbidden` becomes `ApiError` with status 404 and code `forbidden`. A string `detail`
  becomes code `http_<status>` with the string as `detail` (unit test).
- A request still pending after `timeoutMs` rejects with code `timeout`. A fetch failure rejects with code `network`
  (unit test with a mocked `fetch`).
- `messageFor(error)` returns a distinct message for each of `validation_error`, `forbidden`, `precondition_failed`,
  `resource_limit`, `retryable_conflict`, `internal_error`, `engine_unavailable`, `inspector_not_configured`, 401,
  `timeout` and `network` (table-driven unit test). The `forbidden` message is "Not found or not yours".
- `src/api/memory.ts`, `src/api/auth.ts` and `src/api/chat.ts` export one function per route used by the screens.
  Components never call `fetch` (ESLint `no-restricted-globals` rule on `fetch` outside `src/api/`).

### Implementation

```ts
// web/src/api/client.ts
export type TokenKind = "user" | "session" | "none";

export class ApiError extends Error {
  constructor(public status: number, public code: string, public detail: string | null) {
    super(code);
  }
}

let tokenFor: (kind: TokenKind) => string | null = () => null;
export function setTokenSource(source: (kind: TokenKind) => string | null): void {
  tokenFor = source;
}

type Options = { method?: string; json?: unknown; form?: Record<string, string>; auth?: TokenKind; timeoutMs?: number };

export async function request<T>(path: string, options: Options = {}): Promise<T> {
  const { method = "GET", json, form, auth = "user", timeoutMs = 15_000 } = options;
  const headers: Record<string, string> = {};
  const token = auth === "none" ? null : tokenFor(auth);
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = new URLSearchParams(form);
  }
  let response: Response;
  try {
    response = await fetch(path, { method, headers, body, signal: AbortSignal.timeout(timeoutMs) });
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    throw new ApiError(0, timedOut ? "timeout" : "network", null);
  }
  const payload: unknown = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === "object" && "code" in detail) {
      const { code, detail: inner } = detail as { code: string; detail: string | null };
      throw new ApiError(response.status, code, inner ?? null);
    }
    throw new ApiError(response.status, `http_${response.status}`, typeof detail === "string" ? detail : null);
  }
  return payload as T;
}
```

### Files

```text
web/
├── package.json                 # MODIFIED (gen:api script, openapi-typescript)
├── eslint.config.js             # MODIFIED (fetch only inside src/api/)
└── src/api/
    ├── schema.d.ts              # NEW (generated)
    ├── client.ts                # NEW
    ├── client.test.ts           # NEW
    ├── errors.ts                # NEW
    ├── errors.test.ts           # NEW
    ├── auth.ts                  # NEW
    ├── chat.ts                  # NEW
    └── memory.ts                # NEW
```

### Verification

```bash
cd web && npm run gen:api && git diff --exit-code src/api/schema.d.ts
cd web && npx vitest run src/api
cd web && npm run check
```

### Dependencies

- Depends on: WEB-01, APP-07.
- Blocks: WEB-03, CI-03. Every screen uses the client it adds.
- External: **openapi-typescript**.

---

## [WEB-03] · Sign in, register and the two-token session

**Label:** web, feature, security
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-02
**Blocks:** WEB-05

### Objective

Add `/login` with Register and Sign in tabs, and an `AuthProvider` that holds the user token (memory routes, sessions)
and the session token (chat). Protected routes redirect to `/login`. Any 401 clears the tokens and returns there.

### Background and Current State

- `POST /api/v1/auth/register` takes JSON `{email, password, username?}` and returns `id`, `email`, `username`,
  `token.access_token`. `EmailStr` rejects special-use domains.
- `POST /api/v1/auth/login` takes form fields `email`, `password`, `grant_type=password` and returns `access_token`.
- `POST /api/v1/auth/session` (user token) returns `session_id` and `token.access_token`. Chat routes accept only
  this session token.
- Password rules (`app/schemas/auth.py::UserCreate`): 8 to 64 characters, with an upper-case letter, a lower-case
  letter, a digit and a special character. Display name: at most 50 characters.

### Acceptance Criteria

- Register with a new email lands on `/chat` after one submit, holding both a user token and a session token.
- Sign in sends `application/x-www-form-urlencoded` with `grant_type=password` (unit test on the request body).
- A full page reload keeps the user signed in: the state is restored from `sessionStorage`.
- With `sessionStorage` throwing, the app still works in memory (unit test).
- Logout clears both tokens and `sessionStorage`, and the browser back button does not re-enter a protected page.
- A 401 from any request clears the tokens and shows `/login` with the message "Session expired".
- A password that breaks a rule shows the rule inline and sends no request.
- A wrong password shows the message the app returns for a failed login. A duplicate email shows the app's register
  error. Neither text is hard-coded from a guess; each is read from the live response.
- `newChatSession()` creates a session with the user token and replaces the session token. WEB-10 uses it.
- `/about` and `/login` are reachable without signing in. Every other route redirects to `/login`.

### Implementation

```tsx
// web/src/auth/AuthProvider.tsx
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setTokenSource } from "@/api/client";
import { createSession, login as loginRequest } from "@/api/auth";

type AuthState = { email: string; userToken: string; sessionToken: string; sessionId: string } | null;
const KEY = "chronamind.auth";

function load(): AuthState {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "null") as AuthState;
  } catch {
    return null;
  }
}

function save(state: AuthState): void {
  try {
    if (state) sessionStorage.setItem(KEY, JSON.stringify(state));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: the session lives in memory only */
  }
}

const AuthContext = createContext<{
  auth: AuthState; signIn(email: string, password: string): Promise<void>;
  newChatSession(): Promise<void>; logout(): void;
} | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthState>(load);
  useEffect(() => {
    save(auth);
    setTokenSource((kind) => (kind === "user" ? auth?.userToken ?? null : kind === "session" ? auth?.sessionToken ?? null : null));
  }, [auth]);
  const signIn = useCallback(async (email: string, password: string) => {
    const { access_token } = await loginRequest(email, password);
    const session = await createSession(access_token);
    setAuth({ email, userToken: access_token, sessionToken: session.token.access_token, sessionId: session.session_id });
  }, []);
  const newChatSession = useCallback(async () => {
    if (!auth) return;
    const session = await createSession(auth.userToken);
    setAuth({ ...auth, sessionToken: session.token.access_token, sessionId: session.session_id });
  }, [auth]);
  const logout = useCallback(() => setAuth(null), []);
  const value = useMemo(() => ({ auth, signIn, newChatSession, logout }), [auth, signIn, newChatSession, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside AuthProvider");
  return value;
}
```

### Files

```text
web/src/
├── auth/AuthProvider.tsx          # NEW
├── auth/AuthProvider.test.tsx     # NEW
├── auth/RequireAuth.tsx           # NEW
├── pages/Login.tsx                # NEW
├── pages/Login.test.tsx           # NEW
├── router.tsx                     # NEW
└── api/auth.ts                    # MODIFIED (register, login, createSession)
```

### Verification

```bash
cd web && npx vitest run src/auth src/pages/Login.test.tsx
cd web && npm run check
```

### Dependencies

- Depends on: WEB-02.
- Blocks: WEB-05.
- External: **react-router**.

---

## [WEB-04] · App shell and shared memory components

**Label:** web, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-01
**Blocks:** WEB-05, WEB-11

### Objective

Add `AppShell` (navigation rail, top bar with the engine health indicator and the LIVE or DEMO DATA badge) and the
shared components that every memory screen uses. The most important one, `MemorySections`, renders known facts
separately from the verbatim conversation record.

### Background and Current State

- `GET /api/v1/memory/health` returns `{engine: "up" | "down" | "not_configured"}` with no authentication (APP-03).
- `MemoryView` has `known_facts`, `hypotheses`, `procedures`, `conflicts` and `conversation`. Episodes (verbatim turns
  of both speakers, never superseded) are only in `conversation`.
- `VITE_DEMO_MODE` is `live` (default) or `mock` (WEB-13).
- Nothing proposes hypotheses before the W5 cognition runtime, so the hypotheses section is usually empty.

### Acceptance Criteria

- The rail lists, in order, Chat, Memory, Time Travel, Forget, Evidence, and every item is reachable with the
  keyboard, with a visible focus ring.
- The health indicator polls every 15 seconds. It shows "Engine up", "Engine down" or "Engine status unknown" as text
  next to its colour, and a failed poll never blocks the page.
- The badge reads "LIVE" when `VITE_DEMO_MODE` is `live` and "DEMO DATA — recorded rehearsal" when it is `mock`.
- `MemorySections` renders the sections in the order Known facts, Hypotheses, Procedures, Conflicts, then a collapsed
  Conversation record captioned "Verbatim turns (yours and the assistant's). A record of what was said, not facts."
- An item of kind `episode` passed in `known_facts` by mistake is not rendered under Known facts (unit test with a
  mixed view).
- Each channel badge shows a word as well as its colour: facts green, hypotheses violet, procedures blue, conflicts
  amber.
- Item text has `dir="auto"`. An Arabic sample renders right to left inside its row (gallery screenshot).
- The empty Hypotheses section reads "Nothing proposes hypotheses until the reasoning layer (W5)."
- `/dev/components` renders every component in every state, and is routed only when `import.meta.env.DEV` is true.

### Implementation

```tsx
// web/src/components/MemorySections.tsx
import type { components } from "@/api/schema";
import { EmptyState } from "./EmptyState";
import { MemoryItemRow } from "./MemoryItemRow";

type MemoryView = components["schemas"]["MemoryView"];
type MemoryItem = components["schemas"]["MemoryItem"];

const SECTIONS = [
  { key: "known_facts", title: "Known facts", empty: "No facts yet. Tell the assistant something about yourself." },
  { key: "hypotheses", title: "Hypotheses", empty: "Nothing proposes hypotheses until the reasoning layer (W5)." },
  { key: "procedures", title: "Procedures", empty: "No procedures recorded." },
  { key: "conflicts", title: "Conflicts", empty: "No open conflicts." },
] as const;

export function MemorySections({ view, onOpen }: { view: MemoryView; onOpen(id: string): void }) {
  const notEpisode = (item: MemoryItem) => item.kind !== "episode";
  return (
    <div className="flex flex-col gap-4">
      {SECTIONS.map(({ key, title, empty }) => {
        const items = view[key].filter(notEpisode);
        return (
          <section key={key} aria-label={title}>
            <h3 className="text-sm font-semibold">{title} ({items.length})</h3>
            {items.length === 0 ? <EmptyState text={empty} /> : items.map((item) => (
              <MemoryItemRow key={item.id} item={item} onOpen={onOpen} />
            ))}
          </section>
        );
      })}
      <details>
        <summary className="text-sm font-semibold">Conversation record ({view.conversation.length})</summary>
        <p className="text-xs">Verbatim turns (yours and the assistant&apos;s). A record of what was said, not facts.</p>
        {view.conversation.map((item) => <MemoryItemRow key={item.id} item={item} onOpen={onOpen} />)}
      </details>
    </div>
  );
}
```

### Files

```text
web/src/
├── layout/AppShell.tsx             # NEW
├── layout/HealthIndicator.tsx      # NEW
├── components/ChannelBadge.tsx     # NEW
├── components/LifecycleBadge.tsx   # NEW
├── components/MemoryItemRow.tsx    # NEW
├── components/MemorySections.tsx   # NEW
├── components/MemorySections.test.tsx # NEW
├── components/EmptyState.tsx       # NEW
├── components/ErrorBanner.tsx      # NEW
├── components/SlowNotice.tsx       # NEW
├── components/ConfirmDialog.tsx    # NEW
├── components/ui/                  # NEW (shadcn/ui primitives used)
└── pages/DevComponents.tsx         # NEW
```

### Verification

```bash
cd web && npx vitest run src/components src/layout
cd web && npm run check
```

### Dependencies

- Depends on: WEB-01.
- Blocks: WEB-05, WEB-11. Every later screen reuses its components.
- External: **@tanstack/react-query** (health polling), **@radix-ui** primitives via shadcn/ui.

---

## [WEB-05] · Chat page with the live memory panel (journey J1)

**Label:** web, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-03, WEB-04, APP-03
**Blocks:** APP-08

### Objective

Add `/chat`: the conversation on the left and `MemorySections` on the right, fed by `GET /api/v1/memory/`. After
each reply the panel polls until the engine's view settles, so the user sees "I live in Berlin" replaced by "I moved
to Paris" as the single known fact.

### Background and Current State

- `POST /api/v1/chatbot/chat` (session token) takes only the new message, `{"messages": [{"role": "user", "content":
  …}]}`, and returns the session's full message list. The graph state uses the `add_messages` reducer.
- `GET /api/v1/chatbot/messages` (session token) returns the session's history. Content is limited to 1–3000
  characters.
- The app writes memory in a background task after the reply, after one LLM call for slot classification. A new fact
  usually appears within about 10 seconds.
- The LLM call can take up to the app's 60-second total timeout.

### Acceptance Criteria

- On load, the page restores the session's history from `GET /chatbot/messages`.
- Sending posts only the new user message, and the reply replaces the rendered history with the returned list.
- After each successful reply, the panel refetches every 1.5 seconds for up to 15 seconds. It stops early once the set
  of item ids has changed and then stayed the same for two consecutive polls (unit test with fake timers).
- After 15 seconds with no change, the panel shows "No new memory from this turn yet" and a Refresh button.
- Live: on a new user, "I live in Berlin", then "I moved to Paris last week", leaves exactly one residence item under
  Known facts (Paris), with both sentences under Conversation record. This happens within 15 seconds of the second
  reply.
- Live: on another new user, the Arabic pair "أنا ساكن في برلين" then "انتقلت إلى باريس" leaves one residence item.
- One click on a demo chip sends its text. The chips are "I live in Berlin", "I moved to Paris last week",
  "Where do I live?", "أنا ساكن في برلين", "انتقلت إلى باريس".
- Live, with the engine host stopped: chat still returns a reply, the panel shows the engine-unavailable message, and
  the health indicator reads "Engine down".
- The send time of every user message is stored as a turn marker (`{at: ISO string, text}`), at most 50 per user, for
  WEB-09.
- Clicking a memory item navigates to `/memory/:id`.

### Implementation

```tsx
// web/src/features/chat/useMemoryAfterReply.ts
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { listMemory } from "@/api/memory";

const INTERVAL_MS = 1_500;
const WINDOW_MS = 15_000;

function fingerprint(view: Awaited<ReturnType<typeof listMemory>> | undefined): string {
  if (!view) return "";
  return [...view.known_facts, ...view.hypotheses, ...view.procedures, ...view.conflicts, ...view.conversation]
    .map((item) => item.id).sort().join(",");
}

/** Polls the user's memory after each reply until it changes and then settles, or the window closes. */
export function useMemoryAfterReply(userKey: string, replyCount: number) {
  const [pollingSince, setPollingSince] = useState<number | null>(null);
  const before = useRef("");
  const stableFor = useRef(0);
  const query = useQuery({
    queryKey: ["memory", userKey],
    queryFn: () => listMemory(),
    refetchInterval: pollingSince === null ? false : INTERVAL_MS,
  });

  useEffect(() => {
    if (replyCount === 0) return;
    before.current = fingerprint(query.data);
    stableFor.current = 0;
    setPollingSince(Date.now());
  }, [replyCount]); // eslint-disable-line react-hooks/exhaustive-deps -- start a window per reply only

  useEffect(() => {
    if (pollingSince === null) return;
    const current = fingerprint(query.data);
    stableFor.current = current !== before.current ? stableFor.current + 1 : 0;
    if (stableFor.current >= 2 || Date.now() - pollingSince > WINDOW_MS) setPollingSince(null);
  }, [query.dataUpdatedAt, pollingSince]); // eslint-disable-line react-hooks/exhaustive-deps

  const timedOut = pollingSince === null && replyCount > 0 && fingerprint(query.data) === before.current;
  return { ...query, updating: pollingSince !== null, timedOut };
}
```

### Files

```text
web/src/
├── pages/Chat.tsx                              # NEW
├── features/chat/MessageList.tsx               # NEW
├── features/chat/Composer.tsx                  # NEW
├── features/chat/DemoChips.tsx                 # NEW
├── features/chat/useMemoryAfterReply.ts        # NEW
├── features/chat/useMemoryAfterReply.test.ts   # NEW
├── features/chat/turnMarkers.ts                # NEW
└── api/chat.ts                                 # MODIFIED (sendMessage, getHistory)
```

### Verification

```bash
cd web && npx vitest run src/features/chat
cd web && npm run check
uv run python scripts/demo/up.py && cd web && npm run dev   # run J1 by hand; screenshots in the PR
```

### Dependencies

- Depends on: WEB-03, WEB-04, APP-03.
- Blocks: APP-08 (Checkpoint A).
- External: none beyond WEB-01 to WEB-04.

---

## [WEB-06] · Memory Explorer

**Label:** web, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-08
**Blocks:** WEB-07, WEB-08, APP-09

### Objective

Add `/memory`: tabs for Known facts, Hypotheses, Procedures, Conflicts and Conversation record, with counts, and a
search box backed by `POST /api/v1/memory/search`. It is the full view behind journeys J2 and J5.

### Background and Current State

- `GET /api/v1/memory/?limit=500` returns `MemoryView`. `POST /api/v1/memory/search` with `{text}` (2–300 characters)
  returns `MemoryView` for the 20 best matches.
- The chat panel uses the query key `["memory", userKey]`. The Explorer shares it, so a mutation anywhere refreshes
  both.

### Acceptance Criteria

- Each tab label shows its count, and after J1 the counts equal those in the chat panel for the same user.
- Typing fewer than 2 characters shows the hint "Type at least 2 characters" and sends no request.
- Typing 2 or more characters sends one search request after a 300 ms debounce (unit test with fake timers).
- Live: searching "Paris" after J1 lists the Paris claim under Known facts. Searching "zzzz" shows "No matches", not an
  error.
- When an unfiltered list returns exactly `limit` items, the page shows "Showing the first 500".
- Live, as the seeded user: at least 3 known facts and 1 conflict, with the superseded residence only under
  Conversation record.
- Live, with the engine host stopped: an `ErrorBanner` with Retry. After restarting the host, Retry shows the data.
- Clicking a row opens `/memory/:id` over the Explorer.

### Implementation

```tsx
// web/src/pages/Memory.tsx (core)
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { listMemory, searchMemory } from "@/api/memory";
import { useAuth } from "@/auth/AuthProvider";

const LIMIT = 500;

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export function useExplorerData(text: string) {
  const { auth } = useAuth();
  const term = useDebounced(text.trim(), 300);
  const searching = term.length >= 2;
  const all = useQuery({ queryKey: ["memory", auth?.email], queryFn: () => listMemory(LIMIT), enabled: !searching });
  const found = useQuery({
    queryKey: ["memory", auth?.email, "search", term],
    queryFn: () => searchMemory(term),
    enabled: searching,
  });
  const view = searching ? found.data : all.data;
  const total = view
    ? view.known_facts.length + view.hypotheses.length + view.procedures.length + view.conflicts.length +
      view.conversation.length
    : 0;
  return {
    view,
    searching,
    tooShort: text.trim().length > 0 && text.trim().length < 2,
    truncated: !searching && total >= LIMIT,
    error: searching ? found.error : all.error,
    retry: searching ? found.refetch : all.refetch,
  };
}
```

### Files

```text
web/src/
├── pages/Memory.tsx            # NEW
├── pages/Memory.test.tsx       # NEW
└── api/memory.ts               # MODIFIED (listMemory(limit), searchMemory)
```

### Verification

```bash
cd web && npx vitest run src/pages/Memory.test.tsx
cd web && npm run check
```

### Dependencies

- Depends on: APP-08 (Checkpoint A passed).
- Blocks: WEB-07, WEB-08, APP-09.
- External: none.

---

## [WEB-07] · Memory detail drawer (journey J2)

**Label:** web, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-06
**Blocks:** APP-09

### Objective

Add a side sheet at `/memory/:id` that answers "why do you believe that?" from `GET /api/v1/memory/{id}` and
`GET /api/v1/memory/{id}/provenance`. It shows kind, lifecycle, channel, verification and where the memory came from,
and only fields the engine returns.

### Background and Current State

- `MemoryItem` has no timestamps and no quote span. A claim written by the agent stores the user's message verbatim
  as its `content` (`memory_engine._claim_arguments`).
- `SourceRefOut` is `{readable, source_type, derivation_type, ref}`. A turn written by the app has a `ref` starting
  with `app://session/`. An opaque reference has `readable: false` and no source type (`V2-D37`, `I17`).
- `ProvenanceOut` has `edges[{depth, source}]` and `depth_limited`.
- A deleted or foreign id returns 404 with code `forbidden`.

### Acceptance Criteria

- Live: for the Paris claim, the drawer shows lifecycle "asserted", channel "facts", and the source line "From your own
  message in a chat", with the note "The stored text is your message, word for word."
- Live: for a Conversation record item, kind "episode" and lifecycle "observed".
- An opaque source renders "A source in a scope you can't read" and shows no reference value (unit test).
- When `depth_limited` is true, "Show one level deeper" requests `depth + 1`. It is hidden at the router's maximum.
- No timestamp, clock or quote-span field is rendered anywhere in the drawer.
- Opening an id after Forget shows "Not found or not yours (deleted, or never existed)", closes on dismiss, and
  invalidates the `["memory"]` queries.
- Esc closes the drawer and returns focus to the row that opened it.
- The footer link reads "Forget everything about me" and goes to `/forget`. There is no per-item delete control.

### Implementation

```tsx
// web/src/features/memory/describeSource.ts
import type { components } from "@/api/schema";

type SourceRef = components["schemas"]["SourceRefOut"];
type MemoryItem = components["schemas"]["MemoryItem"];

export type SourceLine = { label: string; note?: string; reference?: string };

/** One human line per provenance reference; content is never shown, because the engine never returns it. */
export function describeSource(source: SourceRef, item: MemoryItem): SourceLine {
  if (!source.readable) {
    return { label: "A source in a scope you can't read" };
  }
  if (source.ref.startsWith("app://session/")) {
    return {
      label: "From your own message in a chat",
      note: item.kind === "claim" ? "The stored text is your message, word for word." : undefined,
      reference: source.ref,
    };
  }
  return {
    label: `${source.source_type ?? "source"} (${source.derivation_type})`,
    reference: source.ref,
  };
}
```

### Files

```text
web/src/features/memory/
├── MemoryDrawer.tsx            # NEW
├── MemoryDrawer.test.tsx       # NEW
├── describeSource.ts           # NEW
└── describeSource.test.ts      # NEW
```

### Verification

```bash
cd web && npx vitest run src/features/memory
cd web && npm run check
```

### Dependencies

- Depends on: WEB-06.
- Blocks: APP-09.
- External: none.

---

## [WEB-08] · Resolve a conflict: Confirm or Deny (journey J5)

**Label:** web, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-06, APP-05, APP-06
**Blocks:** APP-09

### Objective

Add Confirm and Deny actions for `contested` items, in the Explorer's Conflicts tab and in the drawer. They call
`POST /api/v1/memory/{id}/adjudicate`. Only the user can turn a held claim into a known fact.

### Background and Current State

- `adjudicate` with `confirm` moves a `contested` claim to `asserted`, and with `deny` to `retired`. The response is
  `AdjudicateResult {id, effect, recorded_at}`.
- An item that is no longer contested returns 409 with code `precondition_failed`.
- The seeded user (APP-06) has one contested claim: "I might switch to a vegetarian diet". A held claim is listed by
  no `query`; it is reachable by id only (`GET /api/v1/memory/{id}`), so the owner's decision in `tickets_app.md`
  (Needs confirmation) fixes how this screen finds it.

### Acceptance Criteria

- The Confirm and Deny buttons render only for items with lifecycle `contested`.
- Each opens a confirm dialog that quotes the item's content. The dialog states "This becomes a known fact." for
  Confirm and "This is retired and no longer shown." for Deny, and accepts an optional note of up to 500 characters.
- While the request is pending, both buttons are disabled, and a double click sends one request (unit test).
- Live, as the seeded user: Confirm moves the claim from Conflicts to Known facts, and the Conflicts count drops by 1.
- Live, after `reset.py` and `seed.py`: Deny removes the claim from every tab.
- A 409 `precondition_failed` shows "This item is no longer contested" and refetches the view.
- Success shows a toast, "Confirmed — now a known fact" or "Denied — retired", and invalidates the `["memory"]`
  queries.

### Implementation

```tsx
// web/src/features/memory/useAdjudicate.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { adjudicate } from "@/api/memory";
import { ApiError } from "@/api/client";
import { toast } from "@/components/Toaster";

export type Decision = "confirm" | "deny";

export function useAdjudicate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision, note }: { id: string; decision: Decision; note?: string }) =>
      adjudicate(id, decision, note),
    onSuccess: (_result, { decision }) => {
      toast(decision === "confirm" ? "Confirmed — now a known fact" : "Denied — retired");
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "precondition_failed") {
        toast("This item is no longer contested");
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["memory"] }),
  });
}
```

### Files

```text
web/src/
├── features/memory/useAdjudicate.ts        # NEW
├── features/memory/AdjudicateButtons.tsx   # NEW
├── features/memory/AdjudicateButtons.test.tsx # NEW
├── pages/Memory.tsx                        # MODIFIED (Conflicts tab actions)
├── features/memory/MemoryDrawer.tsx        # MODIFIED (actions for contested items)
└── api/memory.ts                           # MODIFIED (adjudicate)
```

### Verification

```bash
cd web && npx vitest run src/features/memory
cd web && npm run check
```

### Dependencies

- Depends on: WEB-06, APP-05, APP-06.
- Blocks: APP-09.
- External: none.

---

## [WEB-09] · Time Travel (journey J3)

**Label:** web, feature
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-04, APP-08
**Blocks:** APP-09

### Objective

Add `/time`: a picker for an instant, a "Believed then" column from `GET /api/v1/memory/timeline?at=`, a "Believed
now" column from `GET /api/v1/memory/`, and the list of changes between the two from `GET /api/v1/memory/changes`. It
makes the engine's belief time visible.

### Background and Current State

- `timeline` returns `{at, beliefs, conversation}` for the engine's `audit_as_of` view at that instant. `changes`
  returns `{start, end, changes[{outcome, previous[], current[]}]}`.
- Neither accepts a question text (`V2-D86`), so the page has no question box.
- Instants must carry a timezone and must not be in the future. The router returns 422 otherwise.
- WEB-05 stores turn markers: the client time at which each user message was sent. The memory write for a message
  lands a few seconds after its marker, so the marker of the Paris message is a moment when Berlin is believed and
  Paris is not yet.

### Acceptance Criteria

- The slider spans from the first marker minus 1 minute to now, with a tick at each marker, and hovering a tick shows
  its message text.
- A `datetime-local` input is always available, and is the only picker when the user has no markers (the seeded user).
- A future instant is refused in the page with "Pick a time that is not in the future", and no request is sent.
- Instants are sent as ISO-8601 UTC with an offset and displayed in local time (unit test).
- Live, after J1: at the Paris marker, "Believed then" shows the Berlin claim and not Paris, "Believed now" shows
  Paris, and the changes list shows the residence change with the outcome word the router returned.
- Live: before the first marker, "Believed then" shows "Nothing was believed yet at that time".
- The page header reads "Everything the engine believed at this instant, as recorded then. Belief time comes from the
  database clock."
- A 422 from the router shows its validation message and keeps the page usable.

### Implementation

```tsx
// web/src/features/time/useTimeTravel.ts
import { useQuery } from "@tanstack/react-query";
import { getChanges, getTimeline, listMemory } from "@/api/memory";

export type TurnMarker = { at: string; text: string };

/** The instant as the router expects it: ISO-8601 in UTC with an explicit offset. */
export function toInstant(local: Date): string {
  return local.toISOString().replace("Z", "+00:00");
}

export function isFuture(instant: Date, now: Date = new Date()): boolean {
  return instant.getTime() > now.getTime();
}

export function sliderBounds(markers: TurnMarker[], now: Date = new Date()): { min: number; max: number } {
  const first = markers.length ? Math.min(...markers.map((marker) => Date.parse(marker.at))) : now.getTime();
  return { min: first - 60_000, max: now.getTime() };
}

export function useTimeTravel(userKey: string, instant: Date | null) {
  const valid = instant !== null && !isFuture(instant);
  const at = valid ? toInstant(instant) : "";
  const then = useQuery({ queryKey: ["memory", userKey, "timeline", at], queryFn: () => getTimeline(at), enabled: valid });
  const now = useQuery({ queryKey: ["memory", userKey], queryFn: () => listMemory() });
  const diff = useQuery({
    queryKey: ["memory", userKey, "changes", at],
    queryFn: () => getChanges(at, toInstant(new Date())),
    enabled: valid,
  });
  return { then, now, diff, invalid: instant !== null && !valid };
}
```

### Files

```text
web/src/
├── pages/TimeTravel.tsx                    # NEW
├── features/time/useTimeTravel.ts          # NEW
├── features/time/useTimeTravel.test.ts     # NEW
├── features/time/InstantPicker.tsx         # NEW
└── api/memory.ts                           # MODIFIED (getTimeline, getChanges)
```

### Verification

```bash
cd web && npx vitest run src/features/time
cd web && npm run check
```

### Dependencies

- Depends on: APP-04, APP-08.
- Blocks: APP-09.
- External: none.

---

## [WEB-10] · Forget me (journey J4)

**Label:** web, feature, security
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-05, APP-08
**Blocks:** APP-09

### Objective

Add `/forget`. It shows what will be erased, requires the user to type DELETE, calls `POST /api/v1/memory/forget`,
and renders the content-free receipt and the three observed after-checks. It then starts a new chat session, so the
user can ask again and see that the assistant has no memory left.

### Background and Current State

- `/forget` deletes with scope `all` (per-item deletion is not available through the public API) and clears every
  chat session of the user.
- The response is `ForgetResult`:
  - `receipt`: `deletion_epoch`, `deleted_versions`, `dependents`, `retired_dependents`, `deleted_turns`,
    `deleted_entities`, `deleted_mentions`;
  - `cleared_chat_sessions`;
  - `checks`: `memory_list_empty`, `export_artifact_count`, `old_item_not_found` (`bool | null`).
- `newChatSession()` comes from WEB-03.

### Acceptance Criteria

- Before deleting, the page shows the current counts per section, and the note "This also clears your chat history,
  so the assistant cannot read it back from the conversation."
- The delete button is enabled only when the confirm field contains exactly `DELETE` (unit test).
- Live, after J1: the receipt shows `deleted versions` greater than 0, and every check row renders with its observed
  value.
- A check that is false renders as failed, in red, with its actual value. No check is hidden or rewritten (unit test
  with a failing check).
- `old_item_not_found: null` hides that row.
- After success, `newChatSession()` runs, every `["memory"]` query is invalidated, and the button "Ask the assistant
  again" opens `/chat`.
- Live: in the new session, after "Where do I live?", the memory panel stays empty for the full 15-second poll.
- Live, with the engine host stopped: the error message is shown, and the receipt and checks are not rendered.
- Forget on a user with no memory renders zero counts and passing checks.

### Implementation

```tsx
// web/src/pages/Forget.tsx (core)
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { forgetEverything } from "@/api/memory";
import { useAuth } from "@/auth/AuthProvider";

export function useForget() {
  const queryClient = useQueryClient();
  const { newChatSession } = useAuth();
  return useMutation({
    mutationFn: () => forgetEverything(),
    onSuccess: async () => {
      await newChatSession();
      await queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });
}

export function checkRows(checks: { memory_list_empty: boolean; export_artifact_count: number; old_item_not_found: boolean | null }) {
  const rows = [
    { label: "Memory list is empty", passed: checks.memory_list_empty, value: String(checks.memory_list_empty) },
    {
      label: "Export finds 0 items",
      passed: checks.export_artifact_count === 0,
      value: String(checks.export_artifact_count),
    },
  ];
  if (checks.old_item_not_found !== null) {
    rows.push({
      label: "An item that existed before is now not found",
      passed: checks.old_item_not_found,
      value: String(checks.old_item_not_found),
    });
  }
  return rows;
}

export function useConfirmWord() {
  const [typed, setTyped] = useState("");
  return { typed, setTyped, confirmed: typed === "DELETE" };
}
```

### Files

```text
web/src/
├── pages/Forget.tsx            # NEW
├── pages/Forget.test.tsx       # NEW
└── api/memory.ts               # MODIFIED (forgetEverything)
```

### Verification

```bash
cd web && npx vitest run src/pages/Forget.test.tsx
cd web && npm run check
```

### Dependencies

- Depends on: APP-05, APP-08.
- Blocks: APP-09.
- External: none.

---

## [WEB-11] · Evidence and Status page

**Label:** web, feature, docs
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** WEB-04, APP-08
**Blocks:** APP-09

### Objective

Add `/about`, a public page that puts the live demo in context: the architecture, the status legend, the measured
results with their N and split, and what is not built yet. Its content is typed data in `src/content/evidence.ts`,
with a source for every entry, and its single source is `SHOWCASE.md`.

### Background and Current State

- `SHOWCASE.md` §4 has the architecture (Tier A substrate, Tier B cognition planned for W5, Tier C applications, the
  host, PostgreSQL).
- §8.2 has the measured results, all development-split or engineering measurements. No confirmatory experiment has
  been run (§13).
- §10 lists W4–W8.
- The status legend uses the words Implemented, Partial, Planned and Research design.

### Acceptance Criteria

- Every number on the page equals the number in `SHOWCASE.md` §8.2, and every result row carries its N, the label
  "development split" (or "engineering measurement"), and a source (unit test over `evidence.ts`).
- The results table includes:
  - deletion leak scan: 0/20 development scenarios, ablation 20/20, Clopper–Pearson upper bound 0.168;
  - hybrid retrieval nDCG 0.882 against 0.765 and 0.726 (64 questions over 49 memories);
  - Recall@5 0.982 against 0.393 (same set);
  - relevance-judge no-match false positives 0.125 to 0 (N = 8 no-match questions; the page states the N);
  - deletion-order model: 0 violations in 63,488 cases.
- The sentence "No confirmatory experiment has been run yet." appears under the results.
- The architecture SVG draws Tier B cognition and the Personal Digital Twin as dashed, labelled "Planned".
- The status legend uses words, with no emoji or symbol characters.
- The "Not yet built" list names W4 to W8, one line each.
- The page renders with the app stopped, and the health line shows "Engine status unknown".
- No other page labels a W4–W8 capability as working (review check: search the UI copy for "user model", "twin",
  "reflection", "pattern").

### Implementation

```ts
// web/src/content/evidence.ts
export type Status = "Implemented" | "Partial" | "Planned" | "Research design";

export type Result = {
  measurement: string;
  result: string;
  n: string;
  split: "development split" | "engineering measurement";
  source: string;
};

export const RESULTS: readonly Result[] = [
  {
    measurement: "Deletion leak scan, 11 channels",
    result: "0/20 scenarios leak; ablation leaks 20/20; Clopper–Pearson upper bound 0.168",
    n: "20 scenarios",
    split: "development split",
    source: "SHOWCASE.md §8.2",
  },
  {
    measurement: "Retrieval ranking (nDCG)",
    result: "hybrid 0.882; BM25-reranked 0.765; recency-reranked 0.726",
    n: "64 questions over 49 memories",
    split: "development split",
    source: "SHOWCASE.md §8.2; custom-memory-engine/reports/retrieval/README.md (round 2)",
  },
  {
    measurement: "Value of the dense arm (Recall@5)",
    result: "hybrid 0.982; lexical-only 0.393",
    n: "64 questions over 49 memories",
    split: "development split",
    source: "SHOWCASE.md §8.2; custom-memory-engine/reports/retrieval/README.md (round 2)",
  },
  {
    measurement: "Relevance judge, no-match false positives",
    result: "0.125 to 0 (1/8 to 0/8); 2.73-logit separation over 448 pairs",
    n: "8 no-match questions",
    split: "development split",
    source: "SHOWCASE.md §8.2; custom-memory-engine/reports/retrieval/README.md",
  },
  {
    measurement: "Deletion-order model",
    result: "0 violations (sources-first); 10,838 (dependents-first)",
    n: "63,488 cases (every DAG up to 5 nodes)",
    split: "engineering measurement",
    source: "SHOWCASE.md §5.9 and §8.2",
  },
] as const;

export const NOT_CONFIRMATORY = "No confirmatory experiment has been run yet.";
```

### Files

```text
web/src/
├── pages/About.tsx                 # NEW
├── content/evidence.ts             # NEW
├── content/evidence.test.ts        # NEW
└── content/ArchitectureDiagram.tsx # NEW (inline SVG)
```

### Verification

```bash
cd web && npx vitest run src/content
cd web && npm run check
```

### Dependencies

- Depends on: WEB-04, APP-08.
- Blocks: APP-09.
- External: none.

---

## [WEB-12] · Playwright end-to-end spec of the demo script (live)

**Label:** web, test
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-09
**Blocks:** APP-10

### Objective

Add `web/e2e/demo.spec.ts` and `npm run e2e:live`. The spec drives journeys J1–J5 in a real browser against the live
stack in about 3 minutes, so a late change cannot break the demo unnoticed.

### Background and Current State

- The live stack and seed come from APP-01 and APP-06. `RATE_LIMIT_REGISTER` is raised by `up.py`.
- Memory appears up to 15 seconds after a reply. The LLM's answer text varies between runs.

### Acceptance Criteria

- `npm run e2e:live` runs Chromium against `http://localhost:5173` and passes 3 consecutive times on the demo laptop.
  The PR contains the summary.
- J1 asserts, polling for up to 20 seconds, that Known facts contains exactly one item matching `/Paris/` and none
  matching `/Berlin/`, and that Conversation record contains both sentences.
- J2 asserts "asserted" and "From your own message" in the drawer.
- J3 asserts `/Berlin/` in "Believed then" and `/Paris/` in "Believed now" at the Paris marker.
- J4 asserts every after-check row is passed, then asserts the new session's panel stays empty after "Where do I
  live?" for 15 seconds.
- J5 signs in as the seeded user, confirms the conflict and asserts it is under Known facts. `globalSetup` runs
  `reset.py` and `seed.py`, so the spec can run again.
- No assertion depends on the LLM's answer text.
- On failure, the trace, screenshot and video are kept in `web/test-results/`.
- Showing episodes under Known facts (a deliberate break) makes J1 fail with a readable message. The PR shows this
  once, then reverts it.

### Implementation

```ts
// web/e2e/demo.spec.ts (J1)
import { expect, test } from "@playwright/test";

const email = `demo-${Date.now()}@example.com`;
const password = "Demo-Pass-2026!";

test.describe.configure({ mode: "serial" });

test("J1 remember and update: one current fact, both sentences in the record", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("tab", { name: "Register" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/chat$/);

  for (const chip of ["I live in Berlin", "I moved to Paris last week"]) {
    await page.getByRole("button", { name: chip }).click();
    await expect(page.getByTestId("assistant-message").last()).toBeVisible({ timeout: 70_000 });
  }

  const facts = page.getByRole("region", { name: "Known facts" });
  await expect(async () => {
    await expect(facts.getByTestId("memory-item").filter({ hasText: /Paris/ })).toHaveCount(1);
    await expect(facts.getByTestId("memory-item").filter({ hasText: /Berlin/ })).toHaveCount(0);
  }).toPass({ timeout: 20_000, intervals: [1_500] });

  await page.getByText(/Conversation record/).click();
  const record = page.getByTestId("conversation-record");
  await expect(record).toContainText("I live in Berlin");
  await expect(record).toContainText("I moved to Paris last week");
});
```

### Files

```text
web/
├── playwright.config.ts     # NEW
├── package.json             # MODIFIED (e2e:live script, @playwright/test)
└── e2e/
    ├── demo.spec.ts         # NEW
    └── global-setup.ts      # NEW (reset and seed)
```

### Verification

```bash
uv run python scripts/demo/up.py && uv run python scripts/demo/seed.py
cd web && npx playwright install chromium && npm run e2e:live
cd web && npm run check
```

### Dependencies

- Depends on: APP-09 (Checkpoint B passed).
- Blocks: APP-10.
- External: **@playwright/test**.

---

## [WEB-13] · Mock mode from recorded live responses

**Label:** web, feature, test
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-09
**Blocks:** CI-03, APP-10

### Objective

Add `npm run record-fixtures`, which records every API response of journeys J1–J5 from the live stack, and an MSW
mock mode (`VITE_DEMO_MODE=mock`) that replays them as a small state machine. The badge reads "DEMO DATA — recorded
rehearsal". This is the on-stage fallback and the backend-free target for the CI end-to-end run.

### Background and Current State

- Fixtures recorded from the real API cannot drift from the generated types without failing `tsc`, because each one
  is checked with `satisfies` against `schema.d.ts`.
- The shell already reads `VITE_DEMO_MODE` (WEB-04).
- The Playwright spec exists (WEB-12).

### Acceptance Criteria

- `npm run record-fixtures` runs the J1–J5 API calls against the live stack with `fetch`, and writes one JSON file per
  step plus `manifest.json` (step, method, path, status, recorded_at) under `src/mocks/fixtures/`.
- Recorded fixtures contain no token, no real email (replaced by `demo@example.com`) and no real
  `app://session/` reference (replaced by a stable fake). A unit test greps the fixtures for these patterns.
- Every fixture is imported with `satisfies` against its generated schema type, so a schema change breaks
  `npm run check`.
- `npm run build:mock` produces `dist-mock/`. Served with `npx serve dist-mock`, it runs J1–J5 with no backend.
- In mock mode, the badge text is "DEMO DATA — recorded rehearsal" on every page, and no control hides it.
- `npm run e2e:mock` runs the WEB-12 spec against the mock build and passes.

### Implementation

```ts
// web/src/mocks/handlers.ts (core)
import { http, HttpResponse } from "msw";
import type { components } from "@/api/schema";
import afterBerlin from "./fixtures/memory-after-berlin.json";
import afterParis from "./fixtures/memory-after-paris.json";
import empty from "./fixtures/memory-empty.json";
import forgetResult from "./fixtures/forget.json";

type MemoryView = components["schemas"]["MemoryView"];
type ForgetResult = components["schemas"]["ForgetResult"];

const views = {
  empty: empty satisfies MemoryView,
  berlin: afterBerlin satisfies MemoryView,
  paris: afterParis satisfies MemoryView,
};
let state: keyof typeof views = "empty";

export const handlers = [
  http.post("/api/v1/chatbot/chat", async ({ request }) => {
    const { messages } = (await request.json()) as { messages: { content: string }[] };
    const text = messages.at(-1)?.content ?? "";
    if (/Berlin|برلين/.test(text)) state = "berlin";
    if (/Paris|باريس/.test(text)) state = "paris";
    return HttpResponse.json({ messages: [{ role: "assistant", content: "Recorded reply (demo data)." }] });
  }),
  http.get("/api/v1/memory/", () => HttpResponse.json(views[state])),
  http.post("/api/v1/memory/forget", () => {
    state = "empty";
    return HttpResponse.json(forgetResult satisfies ForgetResult);
  }),
  http.get("/api/v1/memory/health", () => HttpResponse.json({ engine: "up" })),
];
```

### Files

```text
web/
├── package.json                    # MODIFIED (record-fixtures, build:mock, e2e:mock, msw)
├── scripts/record-fixtures.ts      # NEW
├── public/mockServiceWorker.js     # NEW (generated by msw init)
└── src/mocks/
    ├── browser.ts                  # NEW
    ├── handlers.ts                 # NEW
    ├── fixtures/                   # NEW (recorded JSON + manifest.json)
    └── fixtures.test.ts            # NEW (no secrets, emails or session refs)
```

### Verification

```bash
uv run python scripts/demo/up.py && uv run python scripts/demo/seed.py && cd web && npm run record-fixtures
cd web && npm run build:mock && npm run e2e:mock
cd web && npm run check
```

### Dependencies

- Depends on: APP-09.
- Blocks: CI-03, APP-10.
- External: **msw**, **serve** (dev dependency).

---

## [WEB-14] · Failure drills

**Label:** web, test
**Plan task:** none
**Wave / Gate:** none (demo support after W3.5)
**Issue:** Refs #TBD
**Depends on:** APP-09
**Blocks:** APP-10

### Objective

Cause each planned failure deliberately on the live stack and record that the interface shows its planned message,
with no blank page and no stack trace. Commit the drill procedure and the results to `web/README.md` so they can be
repeated before the demo. Defects found become linked fix tickets.

### Background and Current State

- The planned messages come from `messageFor` (WEB-02), `SlowNotice` and `ErrorBanner` (WEB-04).
- Chat keeps working without the engine, because the agent swallows memory errors on purpose.
- `inspector_not_configured` (503) is returned when `MEMORY_ENGINE_USER_TOKEN` is empty.

### Acceptance Criteria

- D1, engine host stopped:
  - chat still replies;
  - the panel and the Explorer show "The memory engine is unavailable" with Retry;
  - the indicator reads "Engine down";
  - after restart, Retry recovers with no page reload.
- D2, app stopped: every page shows the network message with no blank page. After restart, it recovers with no
  reload.
- D3, user token corrupted in `sessionStorage`: the next request lands on `/login` with "Session expired".
- D4, app restarted with an invalid LLM key: chat shows its error inline after the app's timeout, and every memory
  page still works for the seeded user.
- D5, app restarted with `MEMORY_ENGINE_USER_TOKEN` empty: memory pages show "Memory inspector not configured", and
  chat still works.
- D6, engine host paused for 12 seconds: "The memory engine is slow to answer" appears after 10 seconds and clears
  when the host resumes.
- D7: a double-submit of Forget and of Confirm sends one request each.
- Each drill has a PASS or FAIL line with a screenshot in the PR. Each FAIL links a new fix issue.

### Implementation

```markdown
## Failure drills (web/README.md)

Run on the live stack (`scripts/demo/up.py`, then `scripts/demo/seed.py`). Record PASS or FAIL with a screenshot.

| # | Cause | Expected | Result |
|---|---|---|---|
| D1 | Stop the engine host process | Chat replies; memory pages show "The memory engine is unavailable" with Retry; indicator "Engine down"; Retry recovers after restart | |
| D2 | Stop the app process | Network message on every page; recovers after restart without a reload | |
| D3 | Edit `chronamind.auth` in sessionStorage to a bad token | Next request opens /login with "Session expired" | |
| D4 | Restart the app with an invalid LLM key | Chat error inline after the timeout; memory pages work for the seeded user | |
| D5 | Restart the app with `MEMORY_ENGINE_USER_TOKEN` empty | "Memory inspector not configured"; chat works | |
| D6 | Pause the engine host for 12 s | "The memory engine is slow to answer" after 10 s; clears on resume | |
| D7 | Double-click Forget and Confirm | One request each (network tab) | |
```

### Files

```text
web/README.md     # MODIFIED (Failure drills section with results)
```

### Verification

```bash
uv run python scripts/demo/up.py && uv run python scripts/demo/seed.py
cd web && npm run dev
cd web && npm run check
```

### Dependencies

- Depends on: APP-09.
- Blocks: APP-10.
- External: none.

---

## Needs confirmation

- The visible label of the Register submit button and the `data-testid` names used by WEB-12 (`assistant-message`,
  `memory-item`, `conversation-record`) are fixed by WEB-04 and WEB-05. WEB-12 adopts whatever they merged.
- The router's maximum provenance depth (APP-03) bounds the drawer's "Show one level deeper" control (WEB-07).
- The outcome word for the residence change in `mode: change` is recorded in APP-04 and copied by WEB-09.
