import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { SESSION_EXPIRED_MESSAGE, messageFor } from "./errors";

describe("messageFor", () => {
  it.each([
    ["forbidden", 404, "Not found or not yours."],
    ["validation_error", 422, "That request isn't valid."],
    [
      "precondition_failed",
      409,
      "This item can't be changed that way. It may no longer be contested.",
    ],
    [
      "resource_limit",
      429,
      "Too much at once. Please slow down or narrow the search.",
    ],
    ["retryable_conflict", 409, "The memory engine is busy. Please try again."],
    ["internal_error", 502, "The memory engine is unavailable."],
    ["engine_unavailable", 502, "The memory engine is unavailable."],
    [
      "engine_response_invalid",
      502,
      "The memory engine answered in an unexpected way. Please report this.",
    ],
    ["inspector_not_configured", 503, "Memory inspector not configured."],
    ["network", 0, "Can't reach the server. Is the app running?"],
    ["timeout", 0, "The server is taking too long to answer."],
  ])("%s has its own message", (code, status, message) => {
    expect(messageFor(new ApiError(status, code, null))).toBe(message);
  });

  it("names the engine's validation sub-code in words", () => {
    expect(
      messageFor(new ApiError(422, "validation_error", "at_in_future")),
    ).toBe("That request isn't valid (at in future).");
  });

  it("shows the server's own sentence for auth and chat errors", () => {
    expect(
      messageFor(new ApiError(400, "http_400", "Email already registered")),
    ).toBe("Email already registered");
  });

  it("falls back to a session-expired message for a bare 401", () => {
    expect(messageFor(new ApiError(401, "http_401", null))).toBe(
      SESSION_EXPIRED_MESSAGE,
    );
  });

  it("never returns an empty message", () => {
    expect(messageFor(new ApiError(500, "http_500", null))).toBe(
      "Something went wrong (HTTP 500).",
    );
    expect(messageFor(new Error("boom"))).toBe("Something went wrong.");
  });
});
