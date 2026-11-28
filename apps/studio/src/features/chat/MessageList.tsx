import type { ChatMessage } from "@/api/types";
import { cn } from "@/lib/utils";

export interface MessageListProps {
  messages: ChatMessage[];
  /** The message being sent, shown until the reply arrives. */
  pendingText?: string | null;
}

export function MessageList({ messages, pendingText }: MessageListProps) {
  const visible = messages.filter((m) => m.role !== "system");
  if (visible.length === 0 && !pendingText) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        Say something about yourself, then watch the memory panel. Try a
        suggestion below.
      </p>
    );
  }
  return (
    <ol
      role="log"
      aria-label="Conversation"
      aria-live="polite"
      className="flex flex-col gap-3"
    >
      {visible.map((message, index) => (
        <Bubble key={index} role={message.role} content={message.content} />
      ))}
      {pendingText ? (
        <>
          <Bubble role="user" content={pendingText} />
          <li
            className="self-start text-sm text-muted-foreground"
            role="status"
          >
            The assistant is thinking…
          </li>
        </>
      ) : null}
    </ol>
  );
}

function Bubble({
  role,
  content,
}: {
  role: ChatMessage["role"];
  content: string;
}) {
  const mine = role === "user";
  return (
    <li
      data-testid={mine ? "user-message" : "assistant-message"}
      className={cn(
        "max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap",
        mine
          ? "self-end bg-primary text-primary-foreground"
          : "self-start border border-border bg-card text-foreground",
      )}
    >
      <span className="sr-only">{mine ? "You: " : "Assistant: "}</span>
      <span dir="auto">{content}</span>
    </li>
  );
}
