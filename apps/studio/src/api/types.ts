/**
 * Hand-authored TypeScript mirrors of the memory inspector schemas in
 * `docs/api/openapi.json`. Kept in `types.ts` so `schema.d.ts` remains
 * reserved for OpenAPI codegen in WEB-02 (#287).
 */

export type EngineStatus = "up" | "down" | "not_configured";

export interface EngineHealth {
  engine: EngineStatus;
}

export type MemoryKind = "episode" | "claim" | "procedure" | "abstraction";

export type MemoryLifecycle =
  "observed" | "asserted" | "inferred" | "contested" | "retired";

export type MemoryChannel =
  "facts" | "hypotheses" | "procedures" | "conflicts" | "none";

export interface BoundOut {
  support_count: number;
  opportunity_count: number;
  confidence_lower: number;
  coverage: number;
  method_id: string;
  method_version: string;
  verified_on: string;
  expires_at: string;
}

export interface ConflictSideOut {
  id: string;
  lifecycle: MemoryLifecycle;
  text: string;
}

export interface SourceRefOut {
  readable: boolean;
  derivation_type: string;
  ref: string;
  source_type?: string | null;
}

export interface MemoryItem {
  id: string;
  kind: MemoryKind;
  lifecycle: MemoryLifecycle;
  channel: MemoryChannel;
  content: string;
  verification: string;
  label?: string | null;
  bound?: BoundOut | null;
  support_summary?: string;
  conflict_sides?: ConflictSideOut[];
  sources?: SourceRefOut[];
}

export interface MemoryView {
  request_id?: string;
  known_facts: MemoryItem[];
  hypotheses: MemoryItem[];
  procedures: MemoryItem[];
  conflicts: MemoryItem[];
  conversation: MemoryItem[];
}
