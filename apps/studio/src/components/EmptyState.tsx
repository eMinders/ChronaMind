import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  text: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ text, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-2 rounded-md border border-dashed border-border bg-muted/40 px-3 py-3 text-sm text-muted-foreground",
        className,
      )}
    >
      <p>{text}</p>
      {action ? <div>{action}</div> : null}
    </div>
  );
}
