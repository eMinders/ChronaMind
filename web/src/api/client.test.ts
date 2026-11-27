import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  request,
  setTokenSource,
  setUnauthorizedHandler,
} from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function lastCall(fetchMock: ReturnType<typeof vi.fn>) {
  const [path, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  return { path, init, headers: init.headers as Record<string, string> };
}

/** The ApiError a request rejected with; fails the test if it resolved instead. */
async function caught(pending: Promise<unknown>): Promise<ApiError> {
  try {
    await pending;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the request to fail");
}

let fetchMock: ReturnType<typeof vi.fn>;
const unauthorized = vi.fn();

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  setTokenSource((kind) => (kind === "user" ? "user-tok" : "session-tok"));
  setUnauthorizedHandler(unauthorized);
});

afterEach(() => {
  vi.unstubAllGlobals();
  unauthorized.mockReset();
});

describe("request: which token is sent", () => {
  it("sends the user token by default, the session token when asked, and none for public routes", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, {}));

    await request("/api/v1/memory/");
    expect(lastCall(fetchMock).headers.Authorization).toBe("Bearer user-tok");

    await request("/api/v1/chatbot/messages", { auth: "session" });
    expect(lastCall(fetchMock).headers.Authorization).toBe(
      "Bearer session-tok",
    );

    await request("/api/v1/memory/health", { auth: "none" });
    expect(lastCall(fetchMock).headers.Authorization).toBeUndefined();
  });

  it("uses an explicit token when one is passed (right after sign-in)", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, {}));
    await request("/api/v1/auth/session", { method: "POST", token: "fresh" });
    expect(lastCall(fetchMock).headers.Authorization).toBe("Bearer fresh");
  });

  it("sends JSON bodies as JSON and form bodies form-encoded", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, {}));

    await request("/x", { method: "POST", json: { a: 1 } });
    expect(lastCall(fetchMock).headers["Content-Type"]).toBe(
      "application/json",
    );
    expect(lastCall(fetchMock).init.body).toBe('{"a":1}');

    await request("/y", {
      method: "POST",
      auth: "none",
      form: { email: "a@example.com", grant_type: "password" },
    });
    const body = lastCall(fetchMock).init.body as URLSearchParams;
    expect(body).toBeInstanceOf(URLSearchParams);
    expect(body.get("grant_type")).toBe("password");
  });
});

describe("request: error shapes", () => {
  it("reads the memory routes' {detail: {code, detail}} shape", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(404, { detail: { code: "forbidden", detail: null } }),
    );
    const error = await caught(request("/api/v1/memory/v-1"));
    expect([error.status, error.code, error.detail]).toEqual([
      404,
      "forbidden",
      null,
    ]);
  });

  it("reads the app's validation shape into per-field messages", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(422, {
        detail: "Validation error",
        errors: [{ field: "password", message: "Password too short" }],
      }),
    );
    const error = await caught(
      request("/api/v1/auth/register", { auth: "none" }),
    );
    expect(error.code).toBe("validation_error");
    expect(error.fields).toEqual({ password: "Password too short" }); // pragma: allowlist secret
  });

  it("keeps a plain string detail as the message (auth and chat routes)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(400, { detail: "Email already registered" }),
    );
    const error = await caught(
      request("/api/v1/auth/register", { auth: "none" }),
    );
    expect([error.code, error.detail]).toEqual([
      "http_400",
      "Email already registered",
    ]);
  });

  it("survives a non-JSON error body", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>bad gateway</html>", { status: 502 }),
    );
    const error = await caught(request("/x"));
    expect([error.status, error.code]).toEqual([502, "http_502"]);
  });
});

describe("request: who is signed out by a 401", () => {
  it("a 401 on a signed-in call ends the session", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(401, { detail: "Invalid authentication credentials" }),
    );
    await caught(request("/api/v1/memory/"));
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it("a 401 from the login form is a form error, not a sign-out", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(401, { detail: "Incorrect email or password" }),
    );
    const error = await caught(
      request("/api/v1/auth/login", { method: "POST", auth: "none" }),
    );
    expect(error.detail).toBe("Incorrect email or password");
    expect(unauthorized).not.toHaveBeenCalled();
  });
});

describe("request: no answer", () => {
  it("a network failure is `network`", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await caught(request("/x"));
    expect([error.status, error.code]).toEqual([0, "network"]);
  });

  it("a request still pending after its timeout is `timeout`", async () => {
    fetchMock.mockImplementation(
      (_path: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(init.signal?.reason),
          );
        }),
    );
    const error = await caught(request("/x", { timeoutMs: 20 }));
    expect([error.status, error.code]).toEqual([0, "timeout"]);
  });
});
