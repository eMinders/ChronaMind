import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { listMemory } from "@/api/memory";
import {
  POLL_INTERVAL_MS,
  POLL_WINDOW_MS,
  expire,
  fingerprint,
  observe,
  startPolling,
  type PollState,
} from "./memoryPolling";

/**
 * The signed-in user's memory, refreshed after each chat reply until it
 * settles (see `memoryPolling.ts`). `replyCount` goes up by one per reply.
 */
export function useMemoryAfterReply(userKey: string, replyCount: number) {
  const [poll, setPoll] = useState<PollState | null>(null);
  const [pollWindow, setPollWindow] = useState(0);
  const query = useQuery({
    queryKey: ["memory", userKey],
    queryFn: ({ signal }) => listMemory(200, signal),
    refetchInterval: poll?.polling ? POLL_INTERVAL_MS : false,
  });

  // React's pattern for "adjust state when an input changes": compare with the
  // last value seen and update during render, so nothing renders out of date.
  const [seenReply, setSeenReply] = useState(replyCount);
  const [seenUpdate, setSeenUpdate] = useState(query.dataUpdatedAt);
  if (replyCount !== seenReply) {
    // A new reply opens a polling window from what the panel shows now.
    setSeenReply(replyCount);
    setSeenUpdate(query.dataUpdatedAt);
    setPoll(startPolling(fingerprint(query.data)));
    setPollWindow((count) => count + 1);
  } else if (query.dataUpdatedAt !== seenUpdate) {
    // Every refresh is one observation.
    setSeenUpdate(query.dataUpdatedAt);
    if (poll) {
      setPoll(observe(poll, fingerprint(query.data)));
    }
  }

  // The window also closes on time, even when every refresh fails.
  const polling = poll?.polling ?? false;
  useEffect(() => {
    if (!polling) {
      return undefined;
    }
    const timer = setTimeout(
      () => setPoll((state) => (state ? expire(state) : state)),
      POLL_WINDOW_MS,
    );
    return () => clearTimeout(timer);
  }, [polling, pollWindow]);

  return {
    view: query.data,
    error: query.error,
    isLoading: query.isLoading,
    refetch: query.refetch,
    updating: polling,
    noNewMemory: poll ? !poll.polling && poll.noChange : false,
  };
}
