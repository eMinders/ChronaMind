/**
 * The signed-in session, kept outside React.
 *
 * The API client needs the current token synchronously on every request, from
 * the very first one after a page load. A store outside React gives it that
 * with no render or effect timing involved (React runs a child's effects
 * before its parent's, so a provider-registered token arrives too late for a
 * page's first fetch). Components read it with `useSyncExternalStore`.
 */
import { setTokenSource, setUnauthorizedHandler } from "@/api/client";
import type { AuthSession } from "./context";

export const AUTH_STORAGE_KEY = "chronamind.auth";

export interface SessionSnapshot {
  session: AuthSession | null;
  /** Why the last session ended: `"expired"` after a 401, else `null`. */
  signedOutReason: "expired" | null;
}

/** Reads the saved session; storage can be blocked (private mode), so failure means "signed out". */
function load(): AuthSession | null {
  try {
    const raw = sessionStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthSession) : null;
  } catch {
    return null;
  }
}

function save(session: AuthSession | null): void {
  try {
    if (session) {
      sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
    } else {
      sessionStorage.removeItem(AUTH_STORAGE_KEY);
    }
  } catch {
    // Storage blocked: the session lives in memory for this tab only.
  }
}

let snapshot: SessionSnapshot = { session: load(), signedOutReason: null };
const listeners = new Set<() => void>();

function publish(next: SessionSnapshot): void {
  snapshot = next;
  save(next.session);
  for (const listener of listeners) {
    listener();
  }
}

export const sessionStore = {
  get(): SessionSnapshot {
    return snapshot;
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  signIn(session: AuthSession): void {
    publish({ session, signedOutReason: null });
  },
  signOut(): void {
    publish({ session: null, signedOutReason: null });
  },
  /** A signed-in request answered 401: the session is over. */
  expire(): void {
    if (snapshot.session) {
      publish({ session: null, signedOutReason: "expired" });
    }
  },
  /** Re-reads storage, as a page load does. For tests that prepare storage first. */
  reload(): void {
    snapshot = { session: load(), signedOutReason: null };
  },
};

setTokenSource((kind) =>
  kind === "user"
    ? (snapshot.session?.userToken ?? null)
    : (snapshot.session?.sessionToken ?? null),
);
setUnauthorizedHandler(() => sessionStore.expire());
