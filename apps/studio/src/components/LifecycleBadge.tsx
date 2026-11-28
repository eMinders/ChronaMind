import type { MemoryLifecycle } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const LIFECYCLE_STYLE: Record<
  MemoryLifecycle,
  { label: string; badge: string }
> = {
  observed: {
    label: "Observed",
    badge: "border-slate-300 bg-slate-100 text-slate-800",
  },
  asserted: {
    label: "Asserted",
    badge: "border-teal-300 bg-teal-50 text-teal-900",
  },
  inferred: {
    label: "Inferred",
    badge: "border-indigo-300 bg-indigo-50 text-indigo-900",
  },
  contested: {
    label: "Contested",
    badge: "border-amber-300 bg-amber-50 text-amber-950",
  },
  retired: {
    label: "Retired",
    badge: "border-border bg-muted text-muted-foreground line-through",
  },
};

export interface LifecycleBadgeProps {
  lifecycle: MemoryLifecycle;
  className?: string;
}

export function LifecycleBadge({ lifecycle, className }: LifecycleBadgeProps) {
  const style = LIFECYCLE_STYLE[lifecycle];
  return (
    <Badge
      variant="outline"
      data-lifecycle={lifecycle}
      className={cn(style.badge, className)}
    >
      {style.label}
    </Badge>
  );
}
