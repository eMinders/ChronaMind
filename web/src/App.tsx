import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/layout/AppShell";
import { DevComponents } from "@/pages/DevComponents";

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

function PlaceholderPage({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="mx-auto max-w-2xl space-y-2 py-8">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

export interface AppRoutesProps {
  devMode?: boolean;
}

export function AppRoutes({ devMode = import.meta.env.DEV }: AppRoutesProps) {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route
          index
          element={
            <PlaceholderPage
              title="ChronaMind Studio"
              description="Select a workspace view from the navigation rail."
            />
          }
        />
        <Route
          path="/chat"
          element={
            <PlaceholderPage
              title="Chat"
              description="Live chat and side-by-side memory panel arrive in WEB-05."
            />
          }
        />
        <Route
          path="/memory"
          element={
            <PlaceholderPage
              title="Memory"
              description="Memory Explorer arrives in WEB-06."
            />
          }
        />
        <Route
          path="/time"
          element={
            <PlaceholderPage
              title="Time Travel"
              description="Belief-time inspection arrives in WEB-09."
            />
          }
        />
        <Route
          path="/forget"
          element={
            <PlaceholderPage
              title="Forget"
              description="Erasure and verification receipt arrive in WEB-10."
            />
          }
        />
        <Route
          path="/about"
          element={
            <PlaceholderPage
              title="Evidence"
              description="Architecture, status legend, and measured results arrive in WEB-11."
            />
          }
        />
        {devMode ? (
          <Route path="/dev/components" element={<DevComponents />} />
        ) : null}
        <Route
          path="*"
          element={
            <PlaceholderPage
              title="Page not found"
              description="The requested route is not available."
            />
          }
        />
      </Route>
    </Routes>
  );
}

export interface AppProps {
  devMode?: boolean;
}

export function App({ devMode = import.meta.env.DEV }: AppProps) {
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes devMode={devMode} />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
