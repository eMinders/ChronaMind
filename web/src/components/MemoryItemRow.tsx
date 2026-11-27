import { ChevronRight } from "lucide-react";
import type { MemoryItem } from "@/api/types";
import { cn } from "@/lib/utils";
import { ChannelBadge } from "./ChannelBadge";
import { LifecycleBadge } from "./LifecycleBadge";

export interface MemoryItemRowProps {
  item: MemoryItem;
  onOpen: (id: string) => void;
  className?: string;
}

export function MemoryItemRow({ item, onOpen, className }: MemoryItemRowProps) {
  return (
    <button
      type="button"
      onClick={() => onOpen(item.id)}
      className={cn(
        "group flex w-full cursor-pointer items-start justify-between gap-3 rounded-md border border-border bg-card px-3 py-2.5 text-left transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        className,
      )}
    >
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <ChannelBadge channel={item.channel} />
          <LifecycleBadge lifecycle={item.lifecycle} />
          {item.label ? (
            <span className="text-xs font-medium text-muted-foreground">
              {item.label}
            </span>
          ) : null}
        </div>
        <p
          dir="auto"
          className="w-full text-start text-sm leading-relaxed text-foreground"
        >
          {item.content}
        </p>
        {item.support_summary ? (
          <p className="text-xs text-muted-foreground">
            {item.support_summary}
          </p>
        ) : null}
      </div>
      <ChevronRight
        aria-hidden="true"
        className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
      />
    </button>
  );
}
