import { useNavigate } from "react-router-dom";
import { messageFor } from "@/api/errors";
import { ErrorBanner } from "@/components/ErrorBanner";
import { MemorySections } from "@/components/MemorySections";
import { Button } from "@/components/ui/button";
import { useMemoryAfterReply } from "./useMemoryAfterReply";

export interface MemoryPanelProps {
  userKey: string;
  replyCount: number;
}

/** What the memory engine holds for the user, refreshed after each reply. */
export function MemoryPanel({ userKey, replyCount }: MemoryPanelProps) {
  const navigate = useNavigate();
  const memory = useMemoryAfterReply(userKey, replyCount);

  return (
    <section aria-label="Memory" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold tracking-tight">Memory</h2>
        {memory.updating ? (
          <span role="status" className="text-xs text-muted-foreground">
            Updating memory…
          </span>
        ) : null}
      </div>

      {memory.noNewMemory ? (
        <div
          role="status"
          className="flex items-center justify-between gap-2 rounded-md bg-muted px-3 py-2 text-xs"
        >
          <span>No new memory from this turn yet.</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => memory.refetch()}
          >
            Refresh
          </Button>
        </div>
      ) : null}

      {memory.error ? (
        <ErrorBanner
          title="Memory unavailable"
          message={messageFor(memory.error)}
          onRetry={() => memory.refetch()}
          retryLabel="Retry"
        />
      ) : null}

      {memory.isLoading ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading memory…
        </p>
      ) : null}

      {memory.view ? (
        <MemorySections
          view={memory.view}
          onOpen={(id) => navigate(`/memory/${encodeURIComponent(id)}`)}
        />
      ) : null}
    </section>
  );
}
