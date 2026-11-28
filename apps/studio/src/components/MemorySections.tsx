import type { MemoryItem, MemoryView } from "@/api/types";
import { EmptyState } from "./EmptyState";
import { MemoryItemRow } from "./MemoryItemRow";

const SECTIONS = [
  {
    key: "known_facts",
    title: "Known facts",
    empty: "No facts yet. Tell the assistant something about yourself.",
  },
  {
    key: "hypotheses",
    title: "Hypotheses",
    empty: "Nothing proposes hypotheses until the reasoning layer (W5).",
  },
  {
    key: "procedures",
    title: "Procedures",
    empty: "No procedures recorded.",
  },
  {
    key: "conflicts",
    title: "Conflicts",
    empty: "No open conflicts.",
  },
] as const;

export interface MemorySectionsProps {
  view: MemoryView;
  onOpen: (id: string) => void;
}

export function MemorySections({ view, onOpen }: MemorySectionsProps) {
  const notEpisode = (item: MemoryItem) => item.kind !== "episode";

  return (
    <div className="flex flex-col gap-5">
      {SECTIONS.map(({ key, title, empty }) => {
        const items = view[key].filter(notEpisode);
        return (
          <section key={key} aria-label={title} className="space-y-2">
            <h3 className="text-sm font-semibold tracking-tight text-foreground">
              {title} ({items.length})
            </h3>
            {items.length === 0 ? (
              <EmptyState text={empty} />
            ) : (
              <div className="flex flex-col gap-2">
                {items.map((item) => (
                  <MemoryItemRow key={item.id} item={item} onOpen={onOpen} />
                ))}
              </div>
            )}
          </section>
        );
      })}

      <details className="rounded-md border border-border bg-muted/25 p-3">
        <summary className="cursor-pointer list-item text-sm font-semibold tracking-tight text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
          Conversation record ({view.conversation.length})
        </summary>
        <p className="mt-1.5 text-xs text-muted-foreground">
          Verbatim turns (yours and the assistant&apos;s). A record of what was
          said, not facts.
        </p>
        <div className="mt-3 flex flex-col gap-2">
          {view.conversation.length === 0 ? (
            <EmptyState text="No conversation turns recorded yet." />
          ) : (
            view.conversation.map((item) => (
              <MemoryItemRow key={item.id} item={item} onOpen={onOpen} />
            ))
          )}
        </div>
      </details>
    </div>
  );
}
