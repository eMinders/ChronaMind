import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "@/api/client";
import { AppRoutes } from "@/App";
import { AUTH_STORAGE_KEY, sessionStore } from "@/auth/sessionStore";
import { json, stubApi } from "@/test/apiStub";

const SESSION = {
  session_id: "s-1",
  name: "",
  token: { access_token: "session-tok", expires_at: "2027-01-01T00:00:00Z" },
};
const HEALTH = json(200, { engine: "up" });
const GOOD_PASSWORD = "Demo!pass1"; // pragma: allowlist secret

function renderAt(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes devMode={false} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function fillAndSubmit(fields: Record<string, string>, submit: string) {
  const user = userEvent.setup();
  for (const [label, value] of Object.entries(fields)) {
    await user.type(screen.getByLabelText(label), value);
  }
  await user.click(screen.getByRole("button", { name: submit }));
  return user;
}

beforeEach(() => {
  sessionStorage.clear();
  sessionStore.reload();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Register", () => {
  it("creates the account, opens a chat session with the new user token and lands on /chat", async () => {
    const { calls } = stubApi({
      "POST /api/v1/auth/register": json(200, {
        id: 7,
        email: "new@example.com",
        username: null,
        token: { access_token: "user-tok", expires_at: "2027-01-01T00:00:00Z" },
      }),
      "POST /api/v1/auth/session": json(200, SESSION),
      "GET /api/v1/memory/health": HEALTH,
    });
    renderAt("/login");

    await fillAndSubmit(
      { Email: "new@example.com", Password: GOOD_PASSWORD },
      "Create account",
    );

    expect(
      await screen.findByRole("heading", { name: "Chat" }),
    ).toBeInTheDocument();
    const register = calls.find((c) => c.path === "/api/v1/auth/register");
    expect(register?.json).toEqual({
      email: "new@example.com",
      password: GOOD_PASSWORD,
    });
    const session = calls.find((c) => c.path === "/api/v1/auth/session");
    expect(session?.headers.Authorization).toBe("Bearer user-tok");
    expect(
      JSON.parse(sessionStorage.getItem(AUTH_STORAGE_KEY) ?? "{}"),
    ).toEqual({
      email: "new@example.com",
      userToken: "user-tok",
      sessionToken: "session-tok",
      sessionId: "s-1",
    });
    expect(screen.getByText("new@example.com")).toBeInTheDocument();
  });

  it("shows every broken password rule and sends nothing", async () => {
    const { calls } = stubApi({});
    renderAt("/login");

    // '-' is not one of the server's special characters
    await fillAndSubmit(
      { Email: "new@example.com", Password: "Demo-pass1" }, // pragma: allowlist secret
      "Create account",
    );

    expect(screen.getByText(/Needs: A special character/)).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("shows the server's message for an email already registered", async () => {
    stubApi({
      "POST /api/v1/auth/register": json(400, {
        detail: "Email already registered",
      }),
    });
    renderAt("/login");

    await fillAndSubmit(
      { Email: "taken@example.com", Password: GOOD_PASSWORD },
      "Create account",
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Email already registered",
    );
  });
});

describe("Sign in", () => {
  it("sends the credentials as form fields with grant_type=password", async () => {
    const { calls } = stubApi({
      "POST /api/v1/auth/login": json(200, {
        access_token: "user-tok",
        token_type: "bearer",
        expires_at: "2027-01-01T00:00:00Z",
      }),
      "POST /api/v1/auth/session": json(200, SESSION),
      "GET /api/v1/memory/health": HEALTH,
    });
    renderAt("/login");
    await userEvent.setup().click(screen.getByRole("tab", { name: "Sign in" }));

    await fillAndSubmit(
      { Email: "me@example.com", Password: "anything" }, // pragma: allowlist secret
      "Sign in",
    );

    expect(
      await screen.findByRole("heading", { name: "Chat" }),
    ).toBeInTheDocument();
    const login = calls.find((c) => c.path === "/api/v1/auth/login");
    expect(login?.form).toEqual({
      email: "me@example.com",
      password: "anything", // pragma: allowlist secret
      grant_type: "password",
    });
    expect(login?.json).toBeUndefined();
  });

  it("a wrong password is a form error and does not count as an expired session", async () => {
    stubApi({
      "POST /api/v1/auth/login": json(401, {
        detail: "Incorrect email or password",
      }),
    });
    renderAt("/login");
    await userEvent.setup().click(screen.getByRole("tab", { name: "Sign in" }));

    await fillAndSubmit(
      { Email: "me@example.com", Password: "wrong" }, // pragma: allowlist secret
      "Sign in",
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Incorrect email or password",
    );
    expect(screen.queryByText(/Session expired/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("The session", () => {
  const saved = {
    email: "me@example.com",
    userToken: "user-tok",
    sessionToken: "session-tok",
    sessionId: "s-1",
  };

  it("survives a reload (restored from sessionStorage)", async () => {
    sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(saved));
    sessionStore.reload();
    stubApi({ "GET /api/v1/memory/health": HEALTH });
    renderAt("/chat");

    expect(
      await screen.findByRole("heading", { name: "Chat" }),
    ).toBeInTheDocument();
  });

  it("protected pages send a signed-out visitor to sign in", () => {
    stubApi({});
    renderAt("/memory");
    expect(
      screen.getByRole("button", { name: "Create account" }),
    ).toBeInTheDocument();
  });

  it("a 401 on a signed-in call clears the session and says it expired", async () => {
    sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(saved));
    sessionStore.reload();
    stubApi({
      "GET /api/v1/memory/health": HEALTH,
      "GET /api/v1/memory/": json(401, {
        detail: "Invalid authentication credentials",
      }),
    });
    renderAt("/chat");
    await screen.findByRole("heading", { name: "Chat" });

    await act(async () => {
      await request("/api/v1/memory/").catch(() => undefined);
    });

    expect(await screen.findByText(/Session expired/)).toBeInTheDocument();
    expect(sessionStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("sign out clears both tokens and returns to sign in", async () => {
    sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(saved));
    sessionStore.reload();
    stubApi({ "GET /api/v1/memory/health": HEALTH });
    renderAt("/chat");

    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Sign out" }));

    expect(
      screen.getByRole("button", { name: "Create account" }),
    ).toBeInTheDocument();
    expect(sessionStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("still signs in when the browser blocks storage", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    stubApi({
      "POST /api/v1/auth/register": json(200, {
        id: 7,
        email: "new@example.com",
        username: null,
        token: { access_token: "user-tok", expires_at: "2027-01-01T00:00:00Z" },
      }),
      "POST /api/v1/auth/session": json(200, SESSION),
      "GET /api/v1/memory/health": HEALTH,
    });
    renderAt("/login");

    await fillAndSubmit(
      { Email: "new@example.com", Password: GOOD_PASSWORD },
      "Create account",
    );

    expect(
      await screen.findByRole("heading", { name: "Chat" }),
    ).toBeInTheDocument();
  });
});
