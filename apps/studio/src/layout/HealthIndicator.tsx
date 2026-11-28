import { useQuery } from "@tanstack/react-query";
import type { EngineHealth } from "@/api/types";
import { cn } from "@/lib/utils";

export type HealthDisplayState = "up" | "down" | "unknown";

const HEALTH_POLL_INTERVAL_MS = 15_000;

const HEALTH_STYLE: Record<
  HealthDisplayState,
  { label: string; dot: string; pill: string }
> = {
  up: {
    label: "Engine up",
    dot: "bg-emerald-600",
    pill: "border-emerald-300 bg-emerald-50 text-emerald-900",
  },
  down: {
    label: "Engine down",
    dot: "bg-red-600",
    pill: "border-red-300 bg-red-50 text-red-900",
  },
  unknown: {
    label: "Engine status unknown",
    dot: "bg-amber-500",
    pill: "border-amber-300 bg-amber-50 text-amber-950",
  },
};

async function fetchEngineHealth(signal?: AbortSignal): Promise<EngineHealth> {
  const response = await fetch("/api/v1/memory/health", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }
  return (await response.json()) as EngineHealth;
}

export interface HealthIndicatorProps {
  /** Static state override for component galleries and tests. */
  statusOverride?: HealthDisplayState;
  className?: string;
}

export function HealthIndicator({
  statusOverride,
  className,
}: HealthIndicatorProps) {
  const query = useQuery<EngineHealth>({
    queryKey: ["memory-health"],
    queryFn: ({ signal }) => fetchEngineHealth(signal),
    refetchInterval: HEALTH_POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    retry: false,
    throwOnError: false,
    enabled: statusOverride === undefined,
  });

  let displayState: HealthDisplayState;
  if (statusOverride !== undefined) {
    displayState = statusOverride;
  } else if (query.isError || !query.data) {
    displayState = "unknown";
  } else if (query.data.engine === "up") {
    displayState = "up";
  } else if (query.data.engine === "down") {
    displayState = "down";
  } else {
    displayState = "unknown";
  }

  const style = HEALTH_STYLE[displayState];

  return (
    <span
      role="status"
      aria-live="polite"
      data-health={displayState}
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium whitespace-nowrap",
        style.pill,
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn("size-2 shrink-0 rounded-full", style.dot)}
      />
      <span>{style.label}</span>
    </span>
  );
}
