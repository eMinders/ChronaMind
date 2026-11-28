import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HealthIndicator } from "./HealthIndicator";

function createTestClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

function renderWithQuery(ui: React.ReactNode) {
  const client = createTestClient();
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("HealthIndicator", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows 'Engine up' with green styling when health reports up", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ engine: "up" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    renderWithQuery(<HealthIndicator />);

    const status = await screen.findByText("Engine up");
    expect(status).toBeInTheDocument();
    expect(status.closest("[role='status']")?.getAttribute("data-health")).toBe(
      "up",
    );
  });

  it("shows 'Engine down' when health reports down", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ engine: "down" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    renderWithQuery(<HealthIndicator />);

    const status = await screen.findByText("Engine down");
    expect(status).toBeInTheDocument();
    expect(status.closest("[role='status']")?.getAttribute("data-health")).toBe(
      "down",
    );
  });

  it("shows 'Engine status unknown' when health reports not_configured", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ engine: "not_configured" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    renderWithQuery(<HealthIndicator />);

    const status = await screen.findByText("Engine status unknown");
    expect(status).toBeInTheDocument();
    expect(status.closest("[role='status']")?.getAttribute("data-health")).toBe(
      "unknown",
    );
  });

  it("shows 'Engine status unknown' and never blocks sibling content when polling fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));

    renderWithQuery(
      <div>
        <HealthIndicator />
        <main>Page remains interactive</main>
      </div>,
    );

    expect(screen.getByText("Page remains interactive")).toBeInTheDocument();
    expect(
      await screen.findByText("Engine status unknown"),
    ).toBeInTheDocument();
  });

  it("polls GET /api/v1/memory/health every 15 seconds", async () => {
    vi.useFakeTimers();

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ engine: "up" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ engine: "down" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    renderWithQuery(<HealthIndicator />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/memory/health",
      expect.objectContaining({ method: "GET" }),
    );
    expect(screen.getByText("Engine up")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_999);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(screen.getByText("Engine down")).toBeInTheDocument();
  });
});
