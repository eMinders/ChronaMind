import {
  Brain,
  FileCheck2,
  History,
  MessageSquare,
  Trash2,
} from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { HealthIndicator } from "./HealthIndicator";

export type DemoMode = "live" | "mock";

const NAV_ITEMS = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/time", label: "Time Travel", icon: History },
  { to: "/forget", label: "Forget", icon: Trash2 },
  { to: "/about", label: "Evidence", icon: FileCheck2 },
] as const;

export interface ModeBadgeProps {
  mode: DemoMode;
  className?: string;
}

export function ModeBadge({ mode, className }: ModeBadgeProps) {
  const isMock = mode === "mock";
  return (
    <Badge
      variant="outline"
      data-demo-mode={mode}
      className={cn(
        isMock
          ? "border-amber-400 bg-amber-100 text-amber-950"
          : "border-emerald-300 bg-emerald-50 text-emerald-900",
        className,
      )}
    >
      {isMock ? "DEMO DATA — recorded rehearsal" : "LIVE"}
    </Badge>
  );
}

export interface AppShellProps {
  children?: ReactNode;
  demoMode?: DemoMode;
}

export function AppShell({ children, demoMode }: AppShellProps) {
  const resolvedMode: DemoMode =
    demoMode ?? (import.meta.env.VITE_DEMO_MODE === "mock" ? "mock" : "live");

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <span className="text-base font-semibold tracking-tight text-foreground">
            ChronaMind Studio
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <HealthIndicator />
          <ModeBadge mode={resolvedMode} />
        </div>
      </header>

      <div className="flex flex-1 flex-col md:flex-row">
        <aside className="border-b border-border bg-muted/20 md:w-56 md:shrink-0 md:border-r md:border-b-0">
          <nav
            aria-label="Primary navigation"
            className="flex gap-1 overflow-x-auto p-2 md:flex-col md:p-3"
          >
            {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "text-foreground/80 hover:bg-accent hover:text-accent-foreground",
                  )
                }
              >
                <Icon aria-hidden="true" className="size-4 shrink-0" />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
        </aside>

        <main className="min-w-0 flex-1 p-4 sm:p-6">
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
}
