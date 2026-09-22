"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { ChatStream } from "@/components/chat/chat-stream";
import { Composer } from "@/components/chat/composer";
import { getConversation } from "@/lib/chat";
import { questionCount, startedAt } from "@/lib/conversations";
import type { Mention } from "@/lib/mentions";
import { listPapers } from "@/lib/projects";
import { routes } from "@/lib/routes";
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
  // Turns sent from this view since the snapshot below was fetched. The header
  // counts questions and the snapshot never refreshes, so without this the
  // count contradicts the turns on screen after the very first send. Seeded
  // from ?q= for exactly that reason.
  const [sentHere, setSentHere] = useState(searchParams.get("q") ? 1 : 0);

  useEffect(() => {
    getConversation(projectId, cid)
      .then(setDetail)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId, cid]);

  useEffect(() => {
    listPapers(projectId).then(setPapers).catch(() => {});
  }, [projectId]);

  // What the composer held when the last message was sent. The textarea is
  // cleared optimistically, so without this a rejected send (an unknown paper
  // id, a scope over the server's cap, a dropped connection) destroys what the
  // user typed and tells them nothing.
  const [lastSent, setLastSent] = useState<{ text: string; mentions: Mention[] } | null>(null);

  function handleSend() {
    const q = input.trim();
    if (!q || pendingContent) return;
    setLastSent({ text: q, mentions });
    setInput("");
    setPendingMentions(mentions.map((m) => m.paperId));
    setMentions([]);
    setPendingContent(q);
    setSentHere((n) => n + 1);
  }

  function handleSendFailed() {
    if (!lastSent) return;
    // Only into an empty composer. The textarea is disabled while a turn is in
    // flight so this is the normal case, but restoring over something the user
    // did manage to type would be a second way to lose text.
    setInput((current) => (current.trim() ? current : lastSent.text));
    setMentions((current) => (current.length ? current : lastSent.mentions));
    // The turn never landed, so it was never a question.
    setSentHere((n) => Math.max(0, n - 1));
  }

  return (
    <div className="fade-block space-y-6 pb-4">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b pb-5">
        <div className="min-w-0">
          <Link
            href={routes.chat(projectId)}
            className="mb-2 inline-flex items-center gap-1 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" aria-hidden />
            All conversations
          </Link>
          <h1 className="text-xl font-semibold tracking-tight">
            {loading ? "Opening the conversation" : (detail?.title ?? "Conversation not found")}
          </h1>
        </div>
        {detail && (
          <div className="text-right text-[12px] leading-5 text-muted-foreground">
            <p>
              {questionCount(detail.messages, sentHere)} · {startedAt(detail.created_at)}
            </p>
            <p>Every answer is written from this project&rsquo;s papers alone</p>
          </div>
        )}
      </header>

      {loading ? (
        <div className="space-y-4" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-lg bg-muted" />
          ))}
        </div>
      ) : !detail ? (
        <p className="text-[13px] text-muted-foreground">
          This conversation may have been deleted, or you may not have access to it.
        </p>
      ) : (
        <>
          <ChatStream
            projectId={projectId}
            conversationId={cid}
            initialMessages={detail.messages}
            pendingContent={pendingContent}
            pendingMentions={pendingMentions}
            papers={papers}
            onDone={() => setPendingContent(undefined)}
            onError={handleSendFailed}
          />

          {/* The composer sits at the END of the page, in normal flow — the
              page scrolls, the thread does not scroll inside a box of its
              own — but sticks to the bottom of the viewport so a follow-up
              is always one click away in a long conversation. */}
          <Composer
            className="sticky bottom-3 shadow-sm"
            papers={papers}
            value={input}
            onChange={setInput}
            mentions={mentions}
            onMentionsChange={setMentions}
            onSubmit={handleSend}
            disabled={!!pendingContent}
            submitLabel={pendingContent ? "Asking…" : "Ask"}
          />
        </>
      )}
    </div>
  );
}
