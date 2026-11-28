import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthProvider";
import { RequireAuth } from "@/auth/RequireAuth";
import { AppShell } from "@/layout/AppShell";
import { Chat } from "@/pages/Chat";
import { Login } from "@/pages/Login";
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
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
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
          <Route element={<RequireAuth />}>
            <Route path="/chat" element={<Chat />} />
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
              path="/memory/:id"
              element={
                <PlaceholderPage
                  title="Memory item"
                  description="The memory detail drawer arrives in WEB-07."
                />
              }
            />
          </Route>
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
    </AuthProvider>
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
