import { describe, expect, it } from "vitest";
import { memoryItem, memoryView } from "@/api/samples";
import {
  STABLE_POLLS,
  expire,
  fingerprint,
  observe,
  startPolling,
} from "./memoryPolling";

const BERLIN = memoryView({
  known_facts: [
    memoryItem({
      id: "v-berlin",
      kind: "claim",
      lifecycle: "asserted",
      channel: "facts",
      content: "I live in Berlin",
    }),
  ],
});
const PARIS = memoryView({
  known_facts: [
    memoryItem({
      id: "v-paris",
      kind: "claim",
      lifecycle: "asserted",
      channel: "facts",
      content: "I moved to Paris",
    }),
  ],
  conversation: [
    memoryItem({
      id: "ep-2",
      kind: "episode",
      lifecycle: "observed",
      channel: "facts",
      content: "I moved to Paris",
    }),
  ],
});

describe("fingerprint", () => {
  it("is the same for the same items in any order, and empty for no data", () => {
    const a = memoryView({
      known_facts: [BERLIN.known_facts[0], PARIS.known_facts[0]],
    });
    const b = memoryView({
      known_facts: [PARIS.known_facts[0], BERLIN.known_facts[0]],
    });
    expect(fingerprint(a)).toBe(fingerprint(b));
    expect(fingerprint(undefined)).toBe("");
  });

  it("changes when an item is added, removed or replaced", () => {
    expect(fingerprint(BERLIN)).not.toBe(fingerprint(PARIS));
  });
});

describe("the polling rule", () => {
  it("keeps polling while nothing has changed", () => {
    let state = startPolling(fingerprint(BERLIN));
    for (let i = 0; i < 5; i += 1) {
      state = observe(state, fingerprint(BERLIN));
    }
    expect(state.polling).toBe(true);
  });

  it(`stops once a change has held for ${STABLE_POLLS} polls in a row`, () => {
    let state = startPolling(fingerprint(BERLIN));
    state = observe(state, fingerprint(PARIS)); // the change arrives
    expect(state.polling).toBe(true);
    state = observe(state, fingerprint(PARIS)); // held once
    expect(state.polling).toBe(true);
    state = observe(state, fingerprint(PARIS)); // held twice
    expect(state.polling).toBe(false);
    expect(state.noChange).toBe(false);
  });

  it("restarts the count while the memory is still moving (the turn lands before the fact)", () => {
    const turnOnly = memoryView({
      known_facts: BERLIN.known_facts,
      conversation: PARIS.conversation,
    });
    let state = startPolling(fingerprint(BERLIN));
    state = observe(state, fingerprint(turnOnly));
    state = observe(state, fingerprint(turnOnly));
    state = observe(state, fingerprint(PARIS)); // the fact replaces Berlin: a new change
    expect(state.stable).toBe(0);
    expect(state.polling).toBe(true);
  });

  it("when the window closes with no change, says so", () => {
    const state = expire(
      observe(startPolling(fingerprint(BERLIN)), fingerprint(BERLIN)),
    );
    expect(state.polling).toBe(false);
    expect(state.noChange).toBe(true);
  });

  it("when the window closes after a change, does not claim nothing was stored", () => {
    const state = expire(
      observe(startPolling(fingerprint(BERLIN)), fingerprint(PARIS)),
    );
    expect(state.polling).toBe(false);
    expect(state.noChange).toBe(false);
  });

  it("ignores polls after it has stopped", () => {
    const stopped = expire(startPolling("a"));
    expect(observe(stopped, "b")).toBe(stopped);
    expect(expire(stopped)).toBe(stopped);
  });
});
