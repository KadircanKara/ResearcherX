"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChatStream } from "@/components/chat-stream";
import { getConversation } from "@/lib/chat";
import type { ChatConversationDetail } from "@/lib/types";

export default function ConversationPage() {
  const { id: projectId, cid } = useParams<{ id: string; cid: string }>();
  const searchParams = useSearchParams();

  const [detail, setDetail] = useState<ChatConversationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  // ?q= carries the initial question from the new-chat form.
  const [pendingContent, setPendingContent] = useState<string | undefined>(
    searchParams.get("q") ?? undefined
  );

  useEffect(() => {
    getConversation(projectId, cid)
      .then(setDetail)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId, cid]);

  function handleSend() {
    const q = input.trim();
    if (!q || pendingContent) return;
    setInput("");
    setPendingContent(q);
  }

  if (loading) {
    return (
      <div className="space-y-3 py-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-16 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  if (!detail) {
    return <p className="py-8 text-center text-sm text-muted-foreground">Conversation not found.</p>;
  }

  return (
    <div className="flex h-full flex-col">
      <Link
        href={`/research/${projectId}/chat`}
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        All chats
      </Link>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <ChatStream
          projectId={projectId}
          conversationId={cid}
          initialMessages={detail.messages}
          pendingContent={pendingContent}
          onDone={() => setPendingContent(undefined)}
        />
      </div>

      {/* Input bar */}
      <div className="mt-4 flex gap-2 border-t border-border pt-4">
        <textarea
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Ask a follow-up question…"
          disabled={!!pendingContent}
          className="flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
        />
        <Button
          size="sm"
          onClick={handleSend}
          disabled={!input.trim() || !!pendingContent}
          className="self-end"
        >
          <Send className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}
