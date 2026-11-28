import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { SESSION_EXPIRED_MESSAGE, messageFor } from "@/api/errors";
import { useAuth } from "@/auth/context";
import {
  MAX_DISPLAY_NAME,
  looksLikeEmail,
  passwordProblems,
} from "@/auth/passwordRules";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

type Mode = "register" | "signin";

interface LocationState {
  from?: string;
}

const INPUT =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function Field({
  id,
  label,
  error,
  children,
}: {
  id: string;
  label: string;
  error?: string | string[];
  children: React.ReactNode;
}) {
  const messages = error === undefined ? [] : [error].flat();
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {messages.length > 0 ? (
        <ul id={`${id}-error`} className="space-y-0.5 text-xs text-destructive">
          {messages.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function Login() {
  const { auth, signedOutReason, signIn, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as LocationState;

  const [mode, setMode] = useState<Mode>("register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [fieldErrors, setFieldErrors] = useState<
    Record<string, string | string[]>
  >({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (auth) {
    return <Navigate to={state.from ?? "/chat"} replace />;
  }

  function switchTo(next: Mode) {
    setMode(next);
    setFieldErrors({});
    setFormError(null);
  }

  function validate(): Record<string, string | string[]> {
    const problems: Record<string, string | string[]> = {};
    if (!looksLikeEmail(email)) {
      problems.email = "Enter a valid email address";
    }
    if (mode === "register") {
      const rules = passwordProblems(password);
      if (rules.length > 0) {
        problems.password = rules.map((rule) => `Needs: ${rule}`);
      }
      if (displayName.length > MAX_DISPLAY_NAME) {
        problems.username = `At most ${MAX_DISPLAY_NAME} characters`;
      }
    } else if (password.length === 0) {
      problems.password = "Enter your password"; // pragma: allowlist secret
    }
    return problems;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const problems = validate();
    setFieldErrors(problems);
    setFormError(null);
    if (Object.keys(problems).length > 0) {
      return;
    }
    setPending(true);
    try {
      if (mode === "register") {
        await register(email.trim(), password, displayName.trim() || undefined);
      } else {
        await signIn(email.trim(), password);
      }
      navigate(state.from ?? "/chat", { replace: true });
    } catch (error) {
      if (error instanceof ApiError && Object.keys(error.fields).length > 0) {
        setFieldErrors(error.fields);
      }
      setFormError(messageFor(error));
    } finally {
      setPending(false);
    }
  }

  const tabs: ReadonlyArray<{ mode: Mode; label: string }> = [
    { mode: "register", label: "Register" },
    { mode: "signin", label: "Sign in" },
  ];

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>ChronaMind Studio</CardTitle>
          <CardDescription>
            Chat with the agent and see what its memory knows about you.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {signedOutReason === "expired" ? (
            <p
              role="status"
              className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-950"
            >
              {SESSION_EXPIRED_MESSAGE}
            </p>
          ) : null}

          <div
            role="tablist"
            aria-label="Account"
            className="grid grid-cols-2 gap-1 rounded-md bg-muted p-1"
          >
            {tabs.map((tab) => (
              <button
                key={tab.mode}
                type="button"
                role="tab"
                aria-selected={mode === tab.mode}
                onClick={() => switchTo(tab.mode)}
                className={cn(
                  "rounded px-3 py-1.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  mode === tab.mode
                    ? "bg-background shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <form
            onSubmit={onSubmit}
            noValidate
            className="space-y-3"
            aria-label={mode === "register" ? "Register" : "Sign in"}
          >
            <Field id="email" label="Email" error={fieldErrors.email}>
              <input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                aria-invalid={fieldErrors.email !== undefined}
                className={INPUT}
              />
            </Field>
            <Field id="password" label="Password" error={fieldErrors.password}>
              <input
                id="password"
                type="password"
                autoComplete={
                  mode === "register" ? "new-password" : "current-password"
                }
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-invalid={fieldErrors.password !== undefined}
                className={INPUT}
              />
            </Field>
            {mode === "register" ? (
              <Field
                id="username"
                label="Display name (optional)"
                error={fieldErrors.username}
              >
                <input
                  id="username"
                  type="text"
                  autoComplete="nickname"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  className={INPUT}
                />
              </Field>
            ) : null}

            {formError ? (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            ) : null}

            <Button type="submit" className="w-full" disabled={pending}>
              {pending
                ? "Please wait…"
                : mode === "register"
                  ? "Create account"
                  : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
