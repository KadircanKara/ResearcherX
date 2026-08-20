"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ChatStream } from "@/components/chat-stream";
import { MentionTextarea } from "@/components/mention-textarea";
import { RxTheme } from "@/components/rx-theme";
import { getConversation } from "@/lib/chat";
import { questionCount, startedAt } from "@/lib/conversations";
import type { Mention } from "@/lib/mentions";
import { listPapers } from "@/lib/projects";
import type { ChatConversationDetail, Paper } from "@/lib/types";
import "../chat.css";

function BackGlyph() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M9.5 6h-7M5.5 3l-3 3 3 3" />
    </svg>
  );
}

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
    <RxTheme className="rx-ch">
      <div className="rx-shell">
        <header className="rx-head">
          <div>
            <Link href={`/research/${projectId}/chat`} className="rx-backlink">
              <BackGlyph />
              All conversations
            </Link>
            <h1>{loading ? "Opening the conversation" : (detail?.title ?? "Conversation not found")}</h1>
          </div>
          {detail && (
            <div className="rx-meta">
              {questionCount(detail.messages, sentHere)} · {startedAt(detail.created_at)}
              <br />
              Every answer is written from this project&rsquo;s papers alone
            </div>
          )}
        </header>

        {loading ? (
          <div className="rx-chcol" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="rx-chskel" />
            ))}
          </div>
        ) : !detail ? (
          <p className="rx-lede">
            This conversation may have been deleted, or you may not have access to it.
          </p>
        ) : (
          <div className="rx-chcol">
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

            {/* The composer sits at the END of the column, in normal page
                flow, exactly as in the concept — the page scrolls, the thread
                does not scroll inside a box of its own. */}
            <div className="rx-composer">
              <MentionTextarea
                value={input}
                onChange={setInput}
                mentions={mentions}
                onMentionsChange={setMentions}
                papers={papers}
                disabled={!!pendingContent}
                onSubmit={handleSend}
              />
              <div className="rx-bar">
                <span>
                  Type <b>@</b> to name a paper and search only inside it
                </span>
                <button
                  type="button"
                  className="rx-btn rx-push"
                  onClick={handleSend}
                  disabled={!input.trim() || !!pendingContent}
                >
                  Ask
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </RxTheme>
  );
}
