import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getHistory, sendMessage } from "@/api/chat";
import { messageFor } from "@/api/errors";
import type { ChatMessage } from "@/api/types";
import { useAuth, type AuthSession } from "@/auth/context";
import { ErrorBanner } from "@/components/ErrorBanner";
import { SlowNotice } from "@/components/SlowNotice";
import { Composer } from "@/features/chat/Composer";
import { MemoryPanel } from "@/features/chat/MemoryPanel";
import { MessageList } from "@/features/chat/MessageList";
import { recordMarker } from "@/features/chat/turnMarkers";

/** A reply slower than this shows a notice: the model can take up to a minute. */
const SLOW_REPLY_MS = 10_000;

export function Chat() {
  const { auth } = useAuth();
  // RequireAuth keeps signed-out visitors away; this only satisfies the types.
  return auth ? <ChatScreen session={auth} /> : null;
}

function ChatScreen({ session }: { session: AuthSession }) {
  const queryClient = useQueryClient();
  const historyKey = ["chat", session.sessionId];

  const history = useQuery({
    queryKey: historyKey,
    queryFn: ({ signal }) => getHistory(signal),
  });

  const [draft, setDraft] = useState("");
  const [replyCount, setReplyCount] = useState(0);
  const [slow, setSlow] = useState(false);

  const send = useMutation({
    mutationFn: (text: string) => {
      recordMarker(session.email, text);
      return sendMessage(text);
    },
    onSuccess: (messages: ChatMessage[]) => {
      queryClient.setQueryData(historyKey, messages);
      setDraft("");
      setReplyCount((count) => count + 1);
    },
  });

  useEffect(() => {
    if (!send.isPending) {
      return undefined;
    }
    const timer = setTimeout(() => setSlow(true), SLOW_REPLY_MS);
    return () => clearTimeout(timer);
  }, [send.isPending]);

  function onSend(text: string) {
    setSlow(false);
    setDraft(text); // a failed send keeps the text in the box
    send.mutate(text);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(320px,400px)]">
      <section aria-label="Chat" className="flex min-h-[60vh] flex-col gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Chat</h1>

        {history.error ? (
          <ErrorBanner
            title="Couldn't load the conversation"
            message={messageFor(history.error)}
            onRetry={() => history.refetch()}
            retryLabel="Retry"
          />
        ) : null}

        <div className="flex-1 overflow-y-auto rounded-md border border-border bg-muted/20 p-3">
          <MessageList
            messages={history.data ?? []}
            pendingText={send.isPending ? send.variables : null}
          />
        </div>

        <SlowNotice
          visible={slow && send.isPending}
          message="The assistant is slow to answer. It can take up to a minute."
        />
        {send.error ? (
          <ErrorBanner
            title="No reply"
            message={messageFor(send.error)}
            onRetry={() => onSend(draft)}
            retryLabel="Retry"
          />
        ) : null}

        <Composer
          value={draft}
          onChange={setDraft}
          onSend={onSend}
          disabled={send.isPending}
        />
      </section>

      <aside className="lg:border-l lg:border-border lg:pl-6">
        <MemoryPanel userKey={session.email} replyCount={replyCount} />
      </aside>
    </div>
  );
}
