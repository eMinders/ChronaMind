import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";
import { EmptyState } from "./EmptyState";
import { ErrorBanner } from "./ErrorBanner";
import { SlowNotice } from "./SlowNotice";

describe("EmptyState", () => {
  it("renders its message text", () => {
    render(<EmptyState text="No procedures recorded." />);
    expect(screen.getByText("No procedures recorded.")).toBeInTheDocument();
  });
});

describe("ErrorBanner", () => {
  it("renders alert text, optional error code, and retry button", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();

    render(
      <ErrorBanner
        title="Memory service unavailable"
        message="The memory engine did not respond in time."
        code="engine_unavailable"
        onRetry={onRetry}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Memory service unavailable");
    expect(alert).toHaveTextContent(
      "The memory engine did not respond in time.",
    );
    expect(alert).toHaveTextContent("engine_unavailable");

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("omits the retry button when onRetry is not provided", () => {
    render(<ErrorBanner message="Request failed." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Request failed.");
    expect(
      screen.queryByRole("button", { name: "Try again" }),
    ).not.toBeInTheDocument();
  });
});

describe("SlowNotice", () => {
  it("renders a polite status notice when visible", () => {
    render(
      <SlowNotice message="Waiting for the assistant reply (can take up to 60 seconds)." />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(
      "Waiting for the assistant reply (can take up to 60 seconds).",
    );
  });

  it("renders nothing when visible is false", () => {
    const { container } = render(<SlowNotice visible={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ConfirmDialog", () => {
  it("confirms and cancels cleanly", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onOpenChange = vi.fn();

    render(
      <ConfirmDialog
        open
        onOpenChange={onOpenChange}
        title="Confirm memory update"
        description="This updates the stored belief."
        confirmLabel="Apply update"
        onConfirm={onConfirm}
      />,
    );

    expect(
      screen.getByRole("dialog", { name: "Confirm memory update" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Apply update" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("requires matching confirmation text when requireText is provided", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    render(
      <ConfirmDialog
        open
        onOpenChange={vi.fn()}
        title="Forget all memory"
        description="Erases all stored memory and chat sessions."
        confirmLabel="Erase everything"
        destructive
        requireText="DELETE"
        onConfirm={onConfirm}
      />,
    );

    const confirmBtn = screen.getByRole("button", {
      name: "Erase everything",
    });
    expect(confirmBtn).toBeDisabled();

    const input = screen.getByLabelText(/Type DELETE to confirm/i);
    await user.type(input, "DELET");
    expect(confirmBtn).toBeDisabled();

    await user.type(input, "E");
    expect(confirmBtn).toBeEnabled();

    await user.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
