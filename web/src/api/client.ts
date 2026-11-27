/**
 * The one place the web app talks to the server. Components call the typed
 * functions in `auth.ts`, `chat.ts` and `memory.ts`; nothing else calls `fetch`
 * (an ESLint rule enforces it).
 *
 * Two facts of the API shape this file:
 * - There are two tokens. The memory routes take the **user** token and the chat
 *   routes take the **session** token; swapped, they fail confusingly (422 and
 *   404). Every call names the token it needs.
 * - Errors come in two shapes. The memory routes answer
 *   `{"detail": {"code", "detail"}}`; the app's request validation answers
 *   `{"detail": "Validation error", "errors": [{field, message}]}`; other
 *   routes answer `{"detail": "<message>"}`. `ApiError` normalises all three.
 */

export type TokenKind = "user" | "session" | "none";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string | null;
  /** Field-level messages from a request validation error, keyed by field name. */
  readonly fields: Record<string, string>;

  constructor(
    status: number,
    code: string,
    detail: string | null,
    fields: Record<string, string> = {},
  ) {
    super(detail ?? code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.fields = fields;
  }
}

type TokenSource = (kind: Exclude<TokenKind, "none">) => string | null;

let tokenFor: TokenSource = () => null;
let onUnauthorized: () => void = () => {};

/** Called by the auth provider: where the current tokens come from. */
export function setTokenSource(source: TokenSource): void {
  tokenFor = source;
}

/** Called by the auth provider: what to do when a signed-in request answers 401. */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

export const DEFAULT_TIMEOUT_MS = 15_000;

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  /** A JSON body. */
  json?: unknown;
  /** A form body (`application/x-www-form-urlencoded`), e.g. for login. */
  form?: Record<string, string>;
  /** Which token to send. Defaults to the user token. */
  auth?: TokenKind;
  /** An explicit token, for the moment right after sign-in before it is stored. */
  token?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
}

function combinedSignal(timeoutMs: number, outer?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(timeoutMs);
  return outer ? AbortSignal.any([outer, timeout]) : timeout;
}

function errorFrom(status: number, payload: unknown): ApiError {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && "code" in detail) {
    const { code, detail: inner } = detail as {
      code: unknown;
      detail?: unknown;
    };
    return new ApiError(
      status,
      String(code),
      typeof inner === "string" ? inner : null,
    );
  }
  const errors = (payload as { errors?: unknown } | null)?.errors;
  if (Array.isArray(errors)) {
    const fields: Record<string, string> = {};
    for (const entry of errors) {
      const { field, message } = (entry ?? {}) as {
        field?: unknown;
        message?: unknown;
      };
      if (typeof field === "string" && typeof message === "string") {
        fields[field] = message;
      }
    }
    return new ApiError(status, "validation_error", null, fields);
  }
  return new ApiError(
    status,
    `http_${status}`,
    typeof detail === "string" ? detail : null,
  );
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = "GET",
    json,
    form,
    auth = "user",
    token,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    signal,
  } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  const bearer = token ?? (auth === "none" ? null : tokenFor(auth));
  if (bearer) {
    headers.Authorization = `Bearer ${bearer}`;
  }
  let body: BodyInit | undefined;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = new URLSearchParams(form);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body,
      signal: combinedSignal(timeoutMs, signal),
    });
  } catch (error) {
    if (signal?.aborted) {
      throw error; // the caller cancelled (e.g. a query unmounted): not a failure to report
    }
    // By name, not `instanceof DOMException`: the error can come from another
    // realm's DOMException class (e.g. jsdom in tests), and instanceof then fails.
    const timedOut =
      (error as { name?: unknown } | null)?.name === "TimeoutError";
    throw new ApiError(0, timedOut ? "timeout" : "network", null);
  }

  const payload: unknown =
    response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = errorFrom(response.status, payload);
    // Only a 401 on a signed-in call means the session is over. A 401 from the
    // login form itself ("Incorrect email or password") is a form error.
    if (response.status === 401 && auth !== "none") {
      onUnauthorized();
    }
    throw error;
  }
  return payload as T;
}
