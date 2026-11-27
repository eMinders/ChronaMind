import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, AppRoutes } from "./App";

function renderRoute(initialPath: string, devMode: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AppRoutes devMode={devMode} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ engine: "up" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the ChronaMind Studio placeholder route", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "ChronaMind Studio" }),
    ).toBeInTheDocument();
  });

  it("routes /dev/components only when devMode is true and renders every component including Arabic dir='auto'", () => {
    renderRoute("/dev/components", true);

    const main = screen.getByRole("main");

    expect(
      within(main).getByRole("heading", { name: "Component Gallery" }),
    ).toBeInTheDocument();

    const arabicSample = within(main).getByText("أنا ساكن في برلين");
    expect(arabicSample).toHaveAttribute("dir", "auto");

    expect(
      within(main).getAllByText(
        "Nothing proposes hypotheses until the reasoning layer (W5).",
      ).length,
    ).toBeGreaterThan(0);
    expect(within(main).getByText("Engine up")).toBeInTheDocument();
    expect(within(main).getByText("Engine down")).toBeInTheDocument();
    expect(within(main).getByText("Engine status unknown")).toBeInTheDocument();
    expect(within(main).getByText("LIVE")).toBeInTheDocument();
    expect(
      within(main).getByText("DEMO DATA — recorded rehearsal"),
    ).toBeInTheDocument();
  });

  it("does not expose /dev/components when devMode is false", () => {
    renderRoute("/dev/components", false);

    expect(
      screen.queryByRole("heading", { name: "Component Gallery" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
  });
});
