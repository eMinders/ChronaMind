import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders the ChronaMind Studio placeholder route", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "ChronaMind Studio" }),
    ).toBeInTheDocument();
  });
});
