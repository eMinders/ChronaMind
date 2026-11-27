/**
 * The memory inspector routes (`/api/v1/memory`, `docs/memory.md`). They all act
 * as the signed-in user and take the **user** token, except `health`.
 */
import { request } from "./client";
import type {
  AdjudicateResult,
  ChangesView,
  EngineHealth,
  ForgetResult,
  MemoryItemResponse,
  MemoryView,
  ProvenanceOut,
  TimelineView,
} from "./types";

const BASE = "/api/v1/memory";

export function getMemoryHealth(signal?: AbortSignal): Promise<EngineHealth> {
  return request<EngineHealth>(`${BASE}/health`, { auth: "none", signal });
}

export function listMemory(
  limit = 200,
  signal?: AbortSignal,
): Promise<MemoryView> {
  return request<MemoryView>(`${BASE}/?limit=${limit}`, { signal });
}

export function searchMemory(text: string): Promise<MemoryView> {
  return request<MemoryView>(`${BASE}/search`, {
    method: "POST",
    json: { text },
  });
}

export function getMemoryItem(id: string): Promise<MemoryItemResponse> {
  return request<MemoryItemResponse>(`${BASE}/${encodeURIComponent(id)}`);
}

export function getProvenance(id: string, depth = 1): Promise<ProvenanceOut> {
  return request<ProvenanceOut>(
    `${BASE}/${encodeURIComponent(id)}/provenance?depth=${depth}`,
  );
}

/** `at` is an ISO-8601 instant with an offset; `+` must be URL-encoded. */
export function getTimeline(at: string): Promise<TimelineView> {
  return request<TimelineView>(`${BASE}/timeline?at=${encodeURIComponent(at)}`);
}

export function getChanges(start: string, end: string): Promise<ChangesView> {
  return request<ChangesView>(
    `${BASE}/changes?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
  );
}

export function adjudicate(
  id: string,
  decision: "confirm" | "deny",
  note?: string,
): Promise<AdjudicateResult> {
  return request<AdjudicateResult>(
    `${BASE}/${encodeURIComponent(id)}/adjudicate`,
    { method: "POST", json: note ? { decision, note } : { decision } },
  );
}

export function forgetEverything(): Promise<ForgetResult> {
  return request<ForgetResult>(`${BASE}/forget`, {
    method: "POST",
    json: { confirm: "DELETE" },
  });
}
