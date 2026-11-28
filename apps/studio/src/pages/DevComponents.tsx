import { useState } from "react";
import type {
  MemoryChannel,
  MemoryItem,
  MemoryLifecycle,
  MemoryView,
} from "@/api/types";
import { ChannelBadge } from "@/components/ChannelBadge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LifecycleBadge } from "@/components/LifecycleBadge";
import { MemoryItemRow } from "@/components/MemoryItemRow";
import { MemorySections } from "@/components/MemorySections";
import { SlowNotice } from "@/components/SlowNotice";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ModeBadge } from "@/layout/AppShell";
import { HealthIndicator } from "@/layout/HealthIndicator";

const ALL_CHANNELS: readonly MemoryChannel[] = [
  "facts",
  "hypotheses",
  "procedures",
  "conflicts",
  "none",
];

const ALL_LIFECYCLES: readonly MemoryLifecycle[] = [
  "observed",
  "asserted",
  "inferred",
  "contested",
  "retired",
];

const GALLERY_ITEMS: readonly MemoryItem[] = [
  {
    id: "gallery-fact-en",
    kind: "claim",
    lifecycle: "asserted",
    channel: "facts",
    content: "Lives in Paris",
    verification: "verified",
  },
  {
    id: "gallery-fact-ar",
    kind: "claim",
    lifecycle: "asserted",
    channel: "facts",
    content: "أنا ساكن في برلين",
    verification: "verified",
  },
  {
    id: "gallery-hyp-1",
    kind: "abstraction",
    lifecycle: "inferred",
    channel: "hypotheses",
    label: "Seeded sample hypothesis",
    content: "Prefers concise replies in morning sessions.",
    support_summary: "4 of 5 recorded morning turns requested short answers",
    verification: "unverified",
  },
  {
    id: "gallery-proc-1",
    kind: "procedure",
    lifecycle: "asserted",
    channel: "procedures",
    content: "Format meeting follow-ups as a numbered checklist.",
    verification: "verified",
  },
  {
    id: "gallery-conflict-1",
    kind: "claim",
    lifecycle: "contested",
    channel: "conflicts",
    content: "City of residence: Berlin vs Paris",
    verification: "unverified",
  },
  {
    id: "gallery-retired-1",
    kind: "claim",
    lifecycle: "retired",
    channel: "facts",
    content: "Previously lived in Berlin",
    verification: "verified",
  },
];

const POPULATED_VIEW: MemoryView = {
  known_facts: [
    GALLERY_ITEMS[0],
    {
      id: "gallery-fact-ar-paris",
      kind: "claim",
      lifecycle: "asserted",
      channel: "facts",
      content: "انتقلت إلى باريس",
      verification: "verified",
    },
  ],
  hypotheses: [],
  procedures: [GALLERY_ITEMS[3]],
  conflicts: [GALLERY_ITEMS[4]],
  conversation: [
    {
      id: "gallery-ep-1",
      kind: "episode",
      lifecycle: "observed",
      channel: "facts",
      content: "I live in Berlin",
      verification: "verified",
    },
    {
      id: "gallery-ep-2",
      kind: "episode",
      lifecycle: "observed",
      channel: "facts",
      content: "I moved to Paris last week",
      verification: "verified",
    },
  ],
};

const EMPTY_VIEW: MemoryView = {
  known_facts: [],
  hypotheses: [],
  procedures: [],
  conflicts: [],
  conversation: [],
};

export function DevComponents() {
  const [lastOpenedId, setLastOpenedId] = useState<string | null>(null);
  const [standardDialogOpen, setStandardDialogOpen] = useState(false);
  const [destructiveDialogOpen, setDestructiveDialogOpen] = useState(false);
  const [dialogStatus, setDialogStatus] = useState<string>(
    "No confirmation yet",
  );
  const [retryCount, setRetryCount] = useState(0);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          Component Gallery
        </h1>
        <p className="text-sm text-muted-foreground">
          Development-only showcase of shared ChronaMind Studio components in
          every state.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Status indicators &amp; mode badges</CardTitle>
          <CardDescription>
            Health indicator states and live / demo-data badges.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <HealthIndicator statusOverride="up" />
          <HealthIndicator statusOverride="down" />
          <HealthIndicator statusOverride="unknown" />
          <ModeBadge mode="live" />
          <ModeBadge mode="mock" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Channel badges</CardTitle>
          <CardDescription>
            Word + colour for every memory channel.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {ALL_CHANNELS.map((channel) => (
            <ChannelBadge key={channel} channel={channel} />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Lifecycle badges</CardTitle>
          <CardDescription>
            Every artifact lifecycle defined by the memory inspector contract.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {ALL_LIFECYCLES.map((lifecycle) => (
            <LifecycleBadge key={lifecycle} lifecycle={lifecycle} />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            MemoryItemRow (including Arabic RTL via dir=&quot;auto&quot;)
          </CardTitle>
          <CardDescription>
            Last opened item id:{" "}
            <span className="font-mono text-foreground">
              {lastOpenedId ?? "none"}
            </span>
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {GALLERY_ITEMS.map((item) => (
            <MemoryItemRow
              key={item.id}
              item={item}
              onOpen={(id) => setLastOpenedId(id)}
            />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>MemorySections — populated view</CardTitle>
          <CardDescription>
            Known facts, empty Hypotheses (W5 notice), Procedures, Conflicts,
            and collapsed Conversation record.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MemorySections
            view={POPULATED_VIEW}
            onOpen={(id) => setLastOpenedId(id)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>MemorySections — empty view</CardTitle>
          <CardDescription>Every section in its empty state.</CardDescription>
        </CardHeader>
        <CardContent>
          <MemorySections
            view={EMPTY_VIEW}
            onOpen={(id) => setLastOpenedId(id)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>EmptyState, ErrorBanner &amp; SlowNotice</CardTitle>
          <CardDescription>
            Feedback primitives for empty results, errors, and slow responses.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <EmptyState text="No facts yet. Tell the assistant something about yourself." />
          <ErrorBanner
            title="Memory engine unavailable"
            message="The memory engine did not respond. Chat remains available."
            code="engine_unavailable"
            onRetry={() => setRetryCount((count) => count + 1)}
          />
          <p className="text-xs text-muted-foreground">
            Retry clicks: {retryCount}
          </p>
          <ErrorBanner message="A plain error banner without retry button." />
          <SlowNotice message="Still waiting on the assistant reply — this can take up to 60 seconds." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>ConfirmDialog</CardTitle>
          <CardDescription>{dialogStatus}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() => setStandardDialogOpen(true)}
          >
            Open standard dialog
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => setDestructiveDialogOpen(true)}
          >
            Open destructive dialog
          </Button>

          <ConfirmDialog
            open={standardDialogOpen}
            onOpenChange={setStandardDialogOpen}
            title="Confirm adjudication"
            description="Mark the selected claim as confirmed."
            confirmLabel="Confirm claim"
            onConfirm={() => {
              setDialogStatus("Standard dialog confirmed");
              setStandardDialogOpen(false);
            }}
          />

          <ConfirmDialog
            open={destructiveDialogOpen}
            onOpenChange={setDestructiveDialogOpen}
            title="Forget all memory"
            description="This erases every stored memory and clears all chat sessions."
            confirmLabel="Erase everything"
            destructive
            requireText="DELETE"
            onConfirm={() => {
              setDialogStatus("Destructive dialog confirmed");
              setDestructiveDialogOpen(false);
            }}
          />
        </CardContent>
      </Card>
    </div>
  );
}
