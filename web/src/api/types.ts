/**
 * Names for the API shapes the components use. Every type here is an alias of
 * the generated `schema.d.ts` (`npm run gen:api`, from `docs/api/openapi.json`),
 * never a hand-written copy: a backend change that is not regenerated fails the
 * type check instead of the demo.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type EngineHealth = Schemas["EngineHealth"];
export type EngineStatus = EngineHealth["engine"];

export type MemoryItem = Schemas["MemoryItem"];
export type MemoryItemResponse = Schemas["MemoryItemResponse"];
export type MemoryKind = MemoryItem["kind"];
export type MemoryLifecycle = MemoryItem["lifecycle"];
export type MemoryChannel = MemoryItem["channel"];
export type BoundOut = Schemas["BoundOut"];
export type ConflictSideOut = Schemas["ConflictSideOut"];
export type SourceRefOut = Schemas["SourceRefOut"];
export type MemoryView = Schemas["MemoryView"];

export type ProvenanceOut = Schemas["ProvenanceOut"];
export type TimelineView = Schemas["TimelineView"];
export type ChangesView = Schemas["ChangesView"];
export type AdjudicateResult = Schemas["AdjudicateResult"];
export type ForgetResult = Schemas["ForgetResult"];

export type UserResponse = Schemas["UserResponse"];
export type TokenResponse = Schemas["TokenResponse"];
export type SessionResponse = Schemas["SessionResponse"];
export type ChatMessage = Schemas["Message"];
export type ChatResponse = Schemas["ChatResponse"];
