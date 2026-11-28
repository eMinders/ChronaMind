import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { memoryItem, memoryView } from "@/api/samples";
import type { MemoryView } from "@/api/types";
import { AppRoutes } from "@/App";
import { AUTH_STORAGE_KEY, sessionStore } from "@/auth/sessionStore";
import { readMarkers } from "@/features/chat/turnMarkers";
import { json, stubApi, type StubCall } from "@/test/apiStub";

const SESSION = {
  email: "me@example.com",
  userToken: "user-tok",
  sessionToken: "session-tok",
  sessionId: "s-1",
};

const BERLIN_FACT = memoryItem({
  id: "v-berlin",
  kind: "claim",
  lifecycle: "asserted",
  channel: "facts",
  content: "I live in Berlin",
});
const PARIS_FACT = memoryItem({
  id: "v-paris",
  kind: "claim",
  lifecycle: "asserted",
  channel: "facts",
  content: "I moved to Paris last week",
});
const BERLIN_TURN = memoryItem({
  id: "ep-1",
  kind: "episode",
  lifecycle: "observed",
  channel: "facts",
  content: "I live in Berlin",
});
const PARIS_TURN = memoryItem({
  id: "ep-2",
  kind: "episode",
  lifecycle: "observed",
  channel: "facts",
  content: "I moved to Paris last week",
});

const BEFORE = memoryView({
  known_facts: [BERLIN_FACT],
  conversation: [BERLIN_TURN],
});
const AFTER = memoryView({
  known_facts: [PARIS_FACT],
  conversation: [BERLIN_TURN, PARIS_TURN],
});

const HISTORY = [
  { role: "user", content: "I live in Berlin" },
  { role: "assistant", content: "Noted, Berlin." },
];

function renderChat() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/chat"]}>
        <AppRoutes devMode={false} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** A chat API whose memory answers BEFORE until a message is sent, then AFTER. */
function chatApi(
  overrides: Record<
    string,
    ReturnType<typeof json> | ((call: StubCall) => ReturnType<typeof json>)
  > = {},
) {
  let memory: MemoryView = BEFORE;
  return stubApi({
    "GET /api/v1/memory/health": json(200, { engine: "up" }),
    "GET /api/v1/chatbot/messages": json(200, { messages: HISTORY }),
    "GET /api/v1/memory/": () => json(200, memory),
    "POST /api/v1/chatbot/chat": (call) => {
      memory = AFTER;
      const sent = (call.json as { messages: { content: string }[] }).messages;
      return json(200, {
        messages: [
          ...HISTORY,
          ...sent.map((m) => ({ role: "user", content: m.content })),
          { role: "assistant", content: "Got it, Paris." },
        ],
      });
    },
    ...overrides,
  });
}

beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(SESSION));
  sessionStore.reload();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Chat", () => {
  it("restores the session's history with the session token", async () => {
    const { calls } = chatApi();
    renderChat();

    expect(await screen.findByText("Noted, Berlin.")).toBeInTheDocument();
    const history = calls.find((c) => c.path === "/api/v1/chatbot/messages");
    expect(history?.headers.Authorization).toBe("Bearer session-tok");
  });

  it("sends only the new message, shows the reply and clears the box", async () => {
    const { calls } = chatApi();
    renderChat();
    await screen.findByText("Noted, Berlin.");
    const user = userEvent.setup();

    await user.type(
      screen.getByLabelText("Message"),
      "I moved to Paris last week",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Got it, Paris.")).toBeInTheDocument();
    const send = calls.find((c) => c.path === "/api/v1/chatbot/chat");
    expect(send?.json).toEqual({
      messages: [{ role: "user", content: "I moved to Paris last week" }],
    });
    expect(send?.headers.Authorization).toBe("Bearer session-tok");
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("one click on a suggestion sends it, and the send time is kept for Time Travel", async () => {
    const { calls } = chatApi();
    renderChat();
    await screen.findByText("Noted, Berlin.");

    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "انتقلت إلى باريس" }));

    await screen.findByText("Got it, Paris.");
    const send = calls.find((c) => c.path === "/api/v1/chatbot/chat");
    expect(send?.json).toEqual({
      messages: [{ role: "user", content: "انتقلت إلى باريس" }],
    });
    expect(readMarkers(SESSION.email).map((m) => m.text)).toEqual([
      "انتقلت إلى باريس",
    ]);
  });

  it("a failed reply keeps the typed text and offers Retry", async () => {
    chatApi({
      "POST /api/v1/chatbot/chat": json(500, { detail: "llm call timed out" }),
    });
    renderChat();
    await screen.findByText("Noted, Berlin.");
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Message"), "Where do I live?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("llm call timed out")).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toHaveValue("Where do I live?");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("The memory panel", () => {
  it("shows known facts apart from the verbatim conversation record", async () => {
    chatApi();
    renderChat();

    const panel = await screen.findByRole("region", { name: "Memory" });
    const facts = await within(panel).findByRole("region", {
      name: "Known facts",
    });
    expect(within(facts).getByText("I live in Berlin")).toBeInTheDocument();
    expect(within(panel).getByTestId("conversation-record")).toHaveTextContent(
      "I live in Berlin",
    );
  });

  it("after a reply, refreshes until the move shows: one Paris fact, Berlin gone from known facts", async () => {
    chatApi();
    renderChat();
    const panel = await screen.findByRole("region", { name: "Memory" });
    const before = await within(panel).findByRole("region", {
      name: "Known facts",
    });
    await within(before).findByText("I live in Berlin");

    await userEvent
      .setup()
      .click(
        screen.getByRole("button", { name: "I moved to Paris last week" }),
      );
    await screen.findByText("Got it, Paris.");

    await waitFor(
      () => {
        const facts = within(panel).getByRole("region", {
          name: "Known facts",
        });
        expect(within(facts).getAllByTestId("memory-item")).toHaveLength(1);
        expect(facts).toHaveTextContent("I moved to Paris last week");
        expect(facts).not.toHaveTextContent("I live in Berlin");
      },
      { timeout: 5_000 },
    );
    expect(within(panel).getByTestId("conversation-record")).toHaveTextContent(
      "I live in Berlin",
    );
  });

  it("an unavailable engine shows in the panel while the chat keeps working", async () => {
    chatApi({
      "GET /api/v1/memory/": json(502, {
        detail: { code: "engine_unavailable", detail: null },
      }),
    });
    renderChat();

    expect(
      await screen.findByText("The memory engine is unavailable."),
    ).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Where do I live?" }));
    expect(await screen.findByText("Got it, Paris.")).toBeInTheDocument();
  });

  it("clicking a memory opens its detail page", async () => {
    chatApi();
    renderChat();
    const panel = await screen.findByRole("region", { name: "Memory" });

    const facts = await within(panel).findByRole("region", {
      name: "Known facts",
    });
    await userEvent.setup().click(within(facts).getByTestId("memory-item"));

    expect(
      await screen.findByRole("heading", { name: "Memory item" }),
    ).toBeInTheDocument();
  });
});

describe("Retrying a failed message", () => {
  it("does not record a second Time Travel marker for the same message", async () => {
    let attempts = 0;
    chatApi({
      "POST /api/v1/chatbot/chat": () => {
        attempts += 1;
        return attempts === 1
          ? json(500, { detail: "llm call timed out" })
          : json(200, {
              messages: [{ role: "assistant", content: "Recovered." }],
            });
      },
    });
    renderChat();
    await screen.findByText("Noted, Berlin.");
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Where do I live?" }));
    await user.click(await screen.findByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Recovered.")).toBeInTheDocument();
    expect(attempts).toBe(2);
    expect(readMarkers(SESSION.email).map((m) => m.text)).toEqual([
      "Where do I live?",
    ]);
  });
});
