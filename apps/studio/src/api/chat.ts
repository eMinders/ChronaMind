/**
 * Chat routes. They take the **session** token. The server keeps the history
 * (LangGraph checkpointer), so a send carries only the new message and the
 * answer is the session's whole message list.
 */
import { request } from "./client";
import type { ChatMessage, ChatResponse } from "./types";

/** The agent can take up to the app's 60 s LLM budget; leave room for it. */
export const CHAT_TIMEOUT_MS = 90_000;

export function getHistory(signal?: AbortSignal): Promise<ChatMessage[]> {
  return request<ChatResponse>("/api/v1/chatbot/messages", {
    auth: "session",
    signal,
  }).then((response) => response.messages);
}

export function sendMessage(content: string): Promise<ChatMessage[]> {
  return request<ChatResponse>("/api/v1/chatbot/chat", {
    method: "POST",
    auth: "session",
    json: { messages: [{ role: "user", content }] },
    timeoutMs: CHAT_TIMEOUT_MS,
  }).then((response) => response.messages);
}
