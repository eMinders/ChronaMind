import { Clock } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";

export interface SlowNoticeProps {
  message?: string;
  visible?: boolean;
  className?: string;
}

export function SlowNotice({
  message = "Still working — this request can take a moment.",
  visible = true,
  className,
}: SlowNoticeProps) {
  if (!visible) {
    return null;
  }

  return (
    <Alert
      variant="warning"
      role="status"
      aria-live="polite"
      className={cn(className)}
    >
      <div className="flex items-center gap-2.5">
        <Clock aria-hidden="true" className="size-4 shrink-0 text-amber-700" />
        <AlertDescription className="text-amber-950">
          {message}
        </AlertDescription>
      </div>
    </Alert>
  );
}
