import { ApiError } from "./client";

/**
 * One plain sentence for every error the interface can meet. The engine's own
 * codes come from the memory routes (`apps/api/docs/memory.md`, "Errors"); `network`,
 * `timeout` and `http_*` come from the client.
 */
const MESSAGES: Record<string, string> = {
  forbidden: "Not found or not yours.",
  validation_error: "That request isn't valid.",
  precondition_failed:
    "This item can't be changed that way. It may no longer be contested.",
  resource_limit: "Too much at once. Please slow down or narrow the search.",
  retryable_conflict: "The memory engine is busy. Please try again.",
  internal_error: "The memory engine is unavailable.",
  engine_unavailable: "The memory engine is unavailable.",
  engine_response_invalid:
    "The memory engine answered in an unexpected way. Please report this.",
  inspector_not_configured: "Memory inspector not configured.",
  network: "Can't reach the server. Is the app running?",
  timeout: "The server is taking too long to answer.",
};

export const SESSION_EXPIRED_MESSAGE = "Session expired. Please sign in again.";

/** The message to show for any error thrown by the API functions. */
export function messageFor(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong.";
  }
  if (error.code === "validation_error" && error.detail) {
    return `That request isn't valid (${error.detail.replaceAll("_", " ")}).`;
  }
  if (error.code in MESSAGES) {
    return MESSAGES[error.code];
  }
  if (error.status === 401) {
    return error.detail ?? SESSION_EXPIRED_MESSAGE;
  }
  // Auth and chat routes send a readable sentence ("Email already registered").
  return error.detail ?? `Something went wrong (HTTP ${error.status}).`;
}
