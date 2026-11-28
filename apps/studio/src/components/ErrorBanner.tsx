import { AlertCircle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ErrorBannerProps {
  title?: string;
  message: string;
  code?: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}

export function ErrorBanner({
  title = "Something went wrong",
  message,
  code,
  onRetry,
  retryLabel = "Try again",
  className,
}: ErrorBannerProps) {
  return (
    <Alert variant="destructive" role="alert" className={cn(className)}>
      <div className="flex items-start gap-3">
        <AlertCircle
          aria-hidden="true"
          className="mt-0.5 size-4 shrink-0 text-destructive"
        />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <AlertTitle>{title}</AlertTitle>
            {code ? (
              <Badge
                variant="outline"
                className="border-destructive/40 font-mono text-[11px] text-destructive"
              >
                {code}
              </Badge>
            ) : null}
          </div>
          <AlertDescription>{message}</AlertDescription>
        </div>
        {onRetry ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onRetry}
            className="shrink-0 border-destructive/40 bg-background text-destructive hover:bg-destructive/10 hover:text-destructive"
          >
            {retryLabel}
          </Button>
        ) : null}
      </div>
    </Alert>
  );
}
