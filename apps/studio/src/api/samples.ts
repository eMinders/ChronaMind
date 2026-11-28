/**
 * Builders for complete sample API shapes, for the component gallery and tests.
 *
 * The API always sends every field of a `MemoryItem` and a `MemoryView`, so the
 * generated types require them all. These builders fill in the fields a sample
 * does not care about with the API's own defaults, so a sample can never be a
 * shape the API would not send.
 */
import type { MemoryItem, MemoryView } from "./types";

export type MemoryItemSample = Pick<
  MemoryItem,
  "id" | "kind" | "lifecycle" | "channel" | "content"
> &
  Partial<MemoryItem>;

export function memoryItem(sample: MemoryItemSample): MemoryItem {
  return {
    verification: "unverified",
    label: null,
    support_summary: "",
    bound: null,
    conflict_sides: [],
    sources: [],
    ...sample,
  };
}

export function memoryView(sections: Partial<MemoryView> = {}): MemoryView {
  return {
    request_id: "00000000-0000-0000-0000-000000000000",
    known_facts: [],
    hypotheses: [],
    procedures: [],
    conflicts: [],
    conversation: [],
    ...sections,
  };
}
