/**
 * Sign-in routes. Register takes JSON; login takes **form** fields (OAuth2
 * password grant); a chat session is created with the user token and answers a
 * separate session token that only the chat routes accept.
 */
import { request } from "./client";
import type { SessionResponse, TokenResponse, UserResponse } from "./types";

export function register(
  email: string,
  password: string,
  username?: string,
): Promise<UserResponse> {
  return request<UserResponse>("/api/v1/auth/register", {
    method: "POST",
    auth: "none",
    json: username ? { email, password, username } : { email, password },
  });
}

export function login(email: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    auth: "none",
    form: { email, password, grant_type: "password" },
  });
}

/** Creates a chat session; `userToken` is passed explicitly because it may not be stored yet. */
export function createSession(userToken: string): Promise<SessionResponse> {
  return request<SessionResponse>("/api/v1/auth/session", {
    method: "POST",
    token: userToken,
  });
}
