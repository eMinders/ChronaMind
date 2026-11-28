import type { MemoryChannel } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const CHANNEL_STYLE: Record<
  MemoryChannel,
  { label: string; badge: string; dot: string }
> = {
  facts: {
    label: "Facts",
    badge: "border-emerald-300 bg-emerald-50 text-emerald-900",
    dot: "bg-emerald-600",
  },
  hypotheses: {
    label: "Hypotheses",
    badge: "border-violet-300 bg-violet-50 text-violet-900",
    dot: "bg-violet-600",
  },
  procedures: {
    label: "Procedures",
    badge: "border-blue-300 bg-blue-50 text-blue-900",
    dot: "bg-blue-600",
  },
  conflicts: {
    label: "Conflicts",
    badge: "border-amber-300 bg-amber-50 text-amber-950",
    dot: "bg-amber-600",
  },
  none: {
    label: "None",
    badge: "border-border bg-muted text-muted-foreground",
    dot: "bg-muted-foreground",
  },
};

export interface ChannelBadgeProps {
  channel: MemoryChannel;
  className?: string;
}

export function ChannelBadge({ channel, className }: ChannelBadgeProps) {
  const style = CHANNEL_STYLE[channel];
  return (
    <Badge
      variant="outline"
      data-channel={channel}
      className={cn(style.badge, className)}
    >
      <span
        aria-hidden="true"
        className={cn("size-2 shrink-0 rounded-full", style.dot)}
      />
      <span>{style.label}</span>
    </Badge>
  );
}
