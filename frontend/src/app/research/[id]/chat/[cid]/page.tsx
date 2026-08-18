"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChatStream } from "@/components/chat-stream";
import { MentionTextarea } from "@/components/mention-textarea";
import { getConversation } from "@/lib/chat";
import type { Mention } from "@/lib/mentions";
import { listPapers } from "@/lib/projects";
import type { ChatConversationDetail, Paper } from "@/lib/types";

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
  const [mentions, setMentions] = useState<Mention[]>([]);
  const [papers, setPapers] = useState<Paper[]>([]);
  // ?m= carries the paper ids picked on the new-chat page, for the first
  // message only — cleared once the pending message is confirmed.
  const [pendingMentions, setPendingMentions] = useState<string[]>(
    (searchParams.get("m") ?? "").split(",").filter(Boolean)
  );

  useEffect(() => {
    getConversation(projectId, cid)
      .then(setDetail)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId, cid]);

  useEffect(() => {
    listPapers(projectId).then(setPapers).catch(() => {});
  }, [projectId]);

  function handleSend() {
    const q = input.trim();
    if (!q || pendingContent) return;
    setInput("");
    setPendingMentions(mentions.map((m) => m.paperId));
    setMentions([]);
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
          pendingMentions={pendingMentions}
          papers={papers}
          onDone={() => setPendingContent(undefined)}
        />
      </div>

      {/* Input bar */}
      <div className="mt-4 flex gap-2 border-t border-border pt-4">
        <MentionTextarea
          value={input}
          onChange={setInput}
          mentions={mentions}
          onMentionsChange={setMentions}
          papers={papers}
          disabled={!!pendingContent}
          onSubmit={handleSend}
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
