import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
import { createSession, login, register as registerUser } from "@/api/auth";
import { setTokenSource, setUnauthorizedHandler } from "@/api/client";
import { AuthContext, type AuthSession } from "./context";

export const AUTH_STORAGE_KEY = "chronamind.auth";

/** Reads the saved session; storage can be blocked (private mode), so failure means "signed out". */
function loadSession(): AuthSession | null {
  try {
    const raw = sessionStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthSession) : null;
  } catch {
    return null;
  }
}

function saveSession(session: AuthSession | null): void {
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

async function openChatSession(email: string, userToken: string) {
  const chat = await createSession(userToken);
  return {
    email,
    userToken,
    sessionToken: chat.token.access_token,
    sessionId: chat.session_id,
  } satisfies AuthSession;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthSession | null>(loadSession);
  const [signedOutReason, setSignedOutReason] = useState<"expired" | null>(
    null,
  );
  const navigate = useNavigate();
  // The client reads tokens synchronously on every call, so it reads this ref,
  // which is always current, rather than a value captured by an older render.
  const current = useRef(auth);

  const update = useCallback((next: AuthSession | null) => {
    current.current = next;
    saveSession(next);
    setAuth(next);
  }, []);

  useEffect(() => {
    setTokenSource((kind) =>
      kind === "user"
        ? (current.current?.userToken ?? null)
        : (current.current?.sessionToken ?? null),
    );
    setUnauthorizedHandler(() => {
      if (current.current) {
        // No navigation here: the route guard sends the user to /login (keeping
        // where they were). The reason is state, not navigation state, because
        // React Router applies navigations as transitions and a second redirect
        // would replace one that carried it.
        setSignedOutReason("expired");
        update(null);
      }
    });
  }, [update]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const token = await login(email, password);
      update(await openChatSession(email, token.access_token));
      setSignedOutReason(null);
    },
    [update],
  );

  const register = useCallback(
    async (email: string, password: string, username?: string) => {
      const user = await registerUser(email, password, username);
      update(await openChatSession(user.email, user.token.access_token));
      setSignedOutReason(null);
    },
    [update],
  );

  const newChatSession = useCallback(async () => {
    const session = current.current;
    if (session) {
      update(await openChatSession(session.email, session.userToken));
    }
  }, [update]);

  const logout = useCallback(() => {
    setSignedOutReason(null);
    navigate("/login", { replace: true });
    update(null);
  }, [navigate, update]);

  const value = useMemo(
    () => ({ auth, signedOutReason, signIn, register, newChatSession, logout }),
    [auth, signedOutReason, signIn, register, newChatSession, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
