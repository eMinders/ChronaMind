import type { FormEvent, KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { DEMO_CHIPS, MAX_MESSAGE_LENGTH } from "./chatLimits";

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
  disabled?: boolean;
}

export function Composer({ value, onChange, onSend, disabled }: ComposerProps) {
  const text = value.trim();
  const tooLong = value.length > MAX_MESSAGE_LENGTH;

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (text && !tooLong && !disabled) {
      onSend(text);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      submit();
      event.preventDefault();
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5" aria-label="Suggested messages">
        {DEMO_CHIPS.map((chip) => (
          <button
            key={chip}
            type="button"
            dir="auto"
            disabled={disabled}
            onClick={() => onSend(chip)}
            className="rounded-full border border-border bg-background px-3 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {chip}
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="flex items-end gap-2">
        <label htmlFor="chat-message" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-message"
          dir="auto"
          rows={2}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Tell the assistant something about yourself…"
          aria-invalid={tooLong}
          className="min-h-11 flex-1 resize-y rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Button type="submit" disabled={disabled || !text || tooLong}>
          Send
        </Button>
      </form>
      {tooLong ? (
        <p className="text-xs text-destructive">
          At most {MAX_MESSAGE_LENGTH.toLocaleString()} characters per message.
        </p>
      ) : null}
    </div>
  );
}
