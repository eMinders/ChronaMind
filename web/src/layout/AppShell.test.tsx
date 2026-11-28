import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

function renderShell(
  options: {
    initialPath?: string;
    demoMode?: "live" | "mock";
  } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });

  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ engine: "up" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[options.initialPath ?? "/chat"]}>
        <AppShell demoMode={options.demoMode}>
          <p>Main screen body</p>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("renders the navigation rail in exact order with keyboard-reachable links and visible focus ring", async () => {
    const user = userEvent.setup();
    renderShell();

    const nav = screen.getByRole("navigation", { name: "Primary navigation" });
    const links = within(nav).getAllByRole("link");

    expect(links.map((link) => link.textContent?.trim())).toEqual([
      "Chat",
      "Memory",
      "Time Travel",
      "Forget",
      "Evidence",
    ]);

    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/chat",
      "/memory",
      "/time",
      "/forget",
      "/about",
    ]);

    for (const link of links) {
      expect(link.className).toContain("focus-visible:ring-2");
    }

    links[0].focus();
    expect(links[0]).toHaveFocus();
    await user.tab();
    expect(links[1]).toHaveFocus();
  });

  it("shows LIVE badge by default and DEMO DATA — recorded rehearsal when VITE_DEMO_MODE is mock", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "live");
    const { unmount } = renderShell();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(await screen.findByText("Engine up")).toBeInTheDocument();
    unmount();

    vi.stubEnv("VITE_DEMO_MODE", "mock");
    renderShell();
    expect(
      screen.getByText("DEMO DATA — recorded rehearsal"),
    ).toBeInTheDocument();
  });
});
