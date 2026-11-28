/**
 * When to keep refreshing the memory panel after a chat reply.
 *
 * The app writes memory in the background *after* it replies, and it first
 * asks the model whether the message states a current fact ("I moved to
 * Paris"), so a new fact lands a few seconds after the answer. The panel
 * therefore polls after every reply until the memory has changed and then
 * stayed the same for two polls in a row, or until the window closes (a timer
 * in the hook calls `expire`).
 *
 * Pure functions, so the rule is tested exactly without timers.
 */
import type { MemoryView } from "@/api/types";

export const POLL_INTERVAL_MS = 1_500;
export const POLL_WINDOW_MS = 15_000;
/** Polls in a row that must agree after a change before polling stops. */
export const STABLE_POLLS = 2;

export interface PollState {
  /** The memory as it was when the reply arrived. */
  baseline: string;
  /** What the last poll saw. */
  last: string;
  /** How many polls in a row saw the same changed memory. */
  stable: number;
  polling: boolean;
  /** True when the window closed without any change. */
  noChange: boolean;
}

/** A stable identity for "what the memory holds": every item id, sorted. */
export function fingerprint(view: MemoryView | undefined): string {
  if (!view) {
    return "";
  }
  return [
    ...view.known_facts,
    ...view.hypotheses,
    ...view.procedures,
    ...view.conflicts,
    ...view.conversation,
  ]
    .map((item) => item.id)
    .sort()
    .join(",");
}

export function startPolling(baseline: string): PollState {
  return {
    baseline,
    last: baseline,
    stable: 0,
    polling: true,
    noChange: false,
  };
}

/** One poll's result: polling stops once a change has held for `STABLE_POLLS` polls. */
export function observe(state: PollState, seen: string): PollState {
  if (!state.polling) {
    return state;
  }
  const changed = seen !== state.baseline;
  const stable = changed && seen === state.last ? state.stable + 1 : 0;
  return { ...state, last: seen, stable, polling: stable < STABLE_POLLS };
}

/** The window closed: stop, and say whether anything changed at all. */
export function expire(state: PollState): PollState {
  if (!state.polling) {
    return state;
  }
  return { ...state, polling: false, noChange: state.last === state.baseline };
}
