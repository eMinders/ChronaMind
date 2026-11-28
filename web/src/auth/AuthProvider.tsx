import {
  useCallback,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
import { createSession, login, register as registerUser } from "@/api/auth";
import { AuthContext, type AuthSession } from "./context";
import { sessionStore } from "./sessionStore";

export { AUTH_STORAGE_KEY } from "./sessionStore";

async function openChatSession(
  email: string,
  userToken: string,
): Promise<AuthSession> {
  const chat = await createSession(userToken);
  return {
    email,
    userToken,
    sessionToken: chat.token.access_token,
    sessionId: chat.session_id,
  };
}

/**
 * Sign-in for the whole app. The session itself lives in `sessionStore` (so
 * the API client always has the current token); this provider exposes it and
 * the actions to React.
 *
 * A 401 on a signed-in call expires the session inside the store. No
 * navigation happens here: the route guard sends the user to /login (keeping
 * where they were), and the login page reads `signedOutReason`. That reason is
 * store state, not navigation state, because React Router applies navigations
 * as transitions and the guard's redirect would replace one that carried it.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const { session: auth, signedOutReason } = useSyncExternalStore(
    sessionStore.subscribe,
    sessionStore.get,
  );
  const navigate = useNavigate();

  const signIn = useCallback(async (email: string, password: string) => {
    const token = await login(email, password);
    sessionStore.signIn(await openChatSession(email, token.access_token));
  }, []);

  const register = useCallback(
    async (email: string, password: string, username?: string) => {
      const user = await registerUser(email, password, username);
      sessionStore.signIn(
        await openChatSession(user.email, user.token.access_token),
      );
    },
    [],
  );

  const newChatSession = useCallback(async () => {
    const current = sessionStore.get().session;
    if (current) {
      sessionStore.signIn(
        await openChatSession(current.email, current.userToken),
      );
    }
  }, []);

  const logout = useCallback(() => {
    navigate("/login", { replace: true });
    sessionStore.signOut();
  }, [navigate]);

  const value = useMemo(
    () => ({ auth, signedOutReason, signIn, register, newChatSession, logout }),
    [auth, signedOutReason, signIn, register, newChatSession, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
