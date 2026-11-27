import { createContext, useContext } from "react";

/** Who is signed in, and the two tokens the API needs (see `src/api/client.ts`). */
export interface AuthSession {
  email: string;
  /** From register/login: used by the memory routes and to create chat sessions. */
  userToken: string;
  /** From `POST /auth/session`: the only token the chat routes accept. */
  sessionToken: string;
  sessionId: string;
}

export interface AuthContextValue {
  auth: AuthSession | null;
  /** Why the last session ended: `"expired"` after a 401, else `null`. */
  signedOutReason: "expired" | null;
  signIn(email: string, password: string): Promise<void>;
  register(email: string, password: string, username?: string): Promise<void>;
  /** Starts a fresh chat session for the signed-in user (used after "Forget me"). */
  newChatSession(): Promise<void>;
  logout(): void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return value;
}

/** Like `useAuth`, but `null` outside a provider (for shared layout and the component gallery). */
export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}
