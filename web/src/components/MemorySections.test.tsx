import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  MemoryChannel,
  MemoryItem,
  MemoryLifecycle,
  MemoryView,
} from "@/api/types";
import { ChannelBadge } from "./ChannelBadge";
import { LifecycleBadge } from "./LifecycleBadge";
import { MemoryItemRow } from "./MemoryItemRow";
import { MemorySections } from "./MemorySections";

function makeItem(overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: "ver-1",
    kind: "claim",
    lifecycle: "asserted",
    channel: "facts",
    content: "Lives in Paris",
    verification: "verified",
    ...overrides,
  };
}

function emptyView(): MemoryView {
  return {
    known_facts: [],
    hypotheses: [],
    procedures: [],
    conflicts: [],
    conversation: [],
  };
}

describe("ChannelBadge", () => {
  it.each<[MemoryChannel, string, string]>([
    ["facts", "Facts", "emerald"],
    ["hypotheses", "Hypotheses", "violet"],
    ["procedures", "Procedures", "blue"],
    ["conflicts", "Conflicts", "amber"],
    ["none", "None", "muted"],
  ])("shows word and colour for channel %s", (channel, label, colourToken) => {
    render(<ChannelBadge channel={channel} />);

    const badge = screen.getByText(label);
    expect(badge).toBeInTheDocument();
    expect(badge.closest("[data-slot='badge']")?.className).toContain(
      colourToken,
    );
  });
});

describe("LifecycleBadge", () => {
  it.each<[MemoryLifecycle, string]>([
    ["observed", "Observed"],
    ["asserted", "Asserted"],
    ["inferred", "Inferred"],
    ["contested", "Contested"],
    ["retired", "Retired"],
  ])("renders lifecycle %s", (lifecycle, label) => {
    render(<LifecycleBadge lifecycle={lifecycle} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe("MemoryItemRow", () => {
  it("marks Arabic item text with dir='auto' and triggers onOpen on click and keyboard", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const arabicItem = makeItem({
      id: "ver-ar-1",
      content: "أنا ساكن في برلين",
    });

    render(<MemoryItemRow item={arabicItem} onOpen={onOpen} />);

    const content = screen.getByText("أنا ساكن في برلين");
    expect(content).toHaveAttribute("dir", "auto");

    const rowButton = screen.getByRole("button", {
      name: /أنا ساكن في برلين/,
    });
    await user.click(rowButton);
    expect(onOpen).toHaveBeenCalledWith("ver-ar-1");

    rowButton.focus();
    await user.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it("displays optional hypothesis label and support summary when present", () => {
    const item = makeItem({
      id: "ver-hyp-1",
      kind: "abstraction",
      channel: "hypotheses",
      lifecycle: "inferred",
      label: "Prefers evening check-ins",
      support_summary: "4 of 5 recent sessions after 18:00",
      content: "User usually writes after work.",
    });

    render(<MemoryItemRow item={item} onOpen={vi.fn()} />);

    expect(screen.getByText("Prefers evening check-ins")).toBeInTheDocument();
    expect(
      screen.getByText("4 of 5 recent sessions after 18:00"),
    ).toBeInTheDocument();
    expect(screen.getByText("User usually writes after work.")).toHaveAttribute(
      "dir",
      "auto",
    );
  });
});

describe("MemorySections", () => {
  it("renders sections in order: Known facts, Hypotheses, Procedures, Conflicts, then collapsed Conversation record", () => {
    const view: MemoryView = {
      known_facts: [makeItem({ id: "f-1", content: "Lives in Paris" })],
      hypotheses: [],
      procedures: [
        makeItem({
          id: "p-1",
          kind: "procedure",
          channel: "procedures",
          content: "Summarize action items as bullets",
        }),
      ],
      conflicts: [
        makeItem({
          id: "c-1",
          channel: "conflicts",
          lifecycle: "contested",
          content: "City of residence: Berlin vs Paris",
        }),
      ],
      conversation: [
        makeItem({
          id: "ep-1",
          kind: "episode",
          channel: "facts",
          lifecycle: "observed",
          content: "I moved to Paris last week",
        }),
      ],
    };

    const { container } = render(
      <MemorySections view={view} onOpen={vi.fn()} />,
    );

    const sections = Array.from(container.querySelectorAll("section"));
    expect(sections.map((s) => s.getAttribute("aria-label"))).toEqual([
      "Known facts",
      "Hypotheses",
      "Procedures",
      "Conflicts",
    ]);

    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    expect(
      sections[sections.length - 1].compareDocumentPosition(details!),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);

    expect(
      within(details!).getByText("Conversation record (1)"),
    ).toBeInTheDocument();
    expect(
      within(details!).getByText(
        "Verbatim turns (yours and the assistant's). A record of what was said, not facts.",
      ),
    ).toBeInTheDocument();
  });

  it("filters out episode items mistakenly passed in known_facts or other non-conversation sections", () => {
    const view: MemoryView = {
      known_facts: [
        makeItem({ id: "fact-1", kind: "claim", content: "Lives in Paris" }),
        makeItem({
          id: "mistaken-ep",
          kind: "episode",
          lifecycle: "observed",
          content: "User said: I live in Berlin",
        }),
      ],
      hypotheses: [
        makeItem({
          id: "mistaken-hyp-ep",
          kind: "episode",
          channel: "hypotheses",
          content: "Raw turn in hypotheses",
        }),
      ],
      procedures: [],
      conflicts: [],
      conversation: [
        makeItem({
          id: "conv-ep",
          kind: "episode",
          lifecycle: "observed",
          content: "I live in Berlin",
        }),
      ],
    };

    render(<MemorySections view={view} onOpen={vi.fn()} />);

    const knownFactsSection = screen.getByRole("region", {
      name: "Known facts",
    });
    expect(
      within(knownFactsSection).getByText("Known facts (1)"),
    ).toBeInTheDocument();
    expect(
      within(knownFactsSection).getByText("Lives in Paris"),
    ).toBeInTheDocument();
    expect(
      within(knownFactsSection).queryByText("User said: I live in Berlin"),
    ).not.toBeInTheDocument();

    const hypothesesSection = screen.getByRole("region", {
      name: "Hypotheses",
    });
    expect(
      within(hypothesesSection).getByText("Hypotheses (0)"),
    ).toBeInTheDocument();
    expect(
      within(hypothesesSection).queryByText("Raw turn in hypotheses"),
    ).not.toBeInTheDocument();

    expect(screen.getByText("I live in Berlin")).toBeInTheDocument();
  });

  it("renders the exact empty Hypotheses copy when no hypotheses exist", () => {
    render(<MemorySections view={emptyView()} onOpen={vi.fn()} />);

    const hypothesesSection = screen.getByRole("region", {
      name: "Hypotheses",
    });
    expect(
      within(hypothesesSection).getByText(
        "Nothing proposes hypotheses until the reasoning layer (W5).",
      ),
    ).toBeInTheDocument();
  });

  it("forwards item clicks to onOpen", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const view: MemoryView = {
      ...emptyView(),
      known_facts: [
        makeItem({ id: "fact-42", content: "Speaks Arabic and English" }),
      ],
    };

    render(<MemorySections view={view} onOpen={onOpen} />);
    await user.click(
      screen.getByRole("button", { name: /Speaks Arabic and English/ }),
    );
    expect(onOpen).toHaveBeenCalledWith("fact-42");
  });
});
