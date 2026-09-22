"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatCitation, ChatEvent, ChatMessage, Paper } from "@/lib/types";
import { chatMessagesUrl, getConversation } from "@/lib/chat";
import { getDevUserId } from "@/lib/api";
import { AssistantAnswer, StreamingAnswer } from "@/components/chat/assistant-answer";
import { resetChunkCache } from "@/components/chat/citation-hover-card";
import { ScopeBanner } from "@/components/chat/scope-banner";
import { StatusLine } from "@/components/chat/status-line";
import { UserTurn } from "@/components/chat/user-turn";
import { groupTurns } from "@/lib/conversations";
import {
  emptyMentionsNote,
  scopeLine,
  statusLabel,
  type ChatStatus,
  type RetrievingInfo,
} from "@/lib/chat-scope";

// The live SSE consumer. A `fetch` POST with a manual frame parse, NOT an
// `EventSource` — EventSource cannot POST. There are exactly five event
// types (thinking, retrieving, delta, done, error) and one branch each; a new
// one needs ONE branch here, not the two registrations run-stream.tsx needs.

interface Props {
  projectId: string;
  conversationId: string;
  /** Initial messages seeded from the snapshot GET. */
  initialMessages: ChatMessage[];
  /** Called after the assistant message is confirmed (done event). */
  onDone?: (citations: ChatCitation[]) => void;
  /** Called when the stream fails — parent should unblock the input. */
  onError?: (message: string) => void;
  /** Content of the message that was just submitted (optimistic display). */
  pendingContent?: string;
  /** Paper ids the pending message was scoped to. */
  pendingMentions?: string[];
  /** Full paper list, for resolving mention ids to titles at render time. */
  papers: Paper[];
}

/**
 * Why a send was refused, in the user's terms.
 *
 * The two rejections a mention can cause are indistinguishable from "Request
 * failed.", and both are the user's to fix: a 400 means a mentioned paper is
 * no longer in the project (deleted in another tab between the pick and the
 * send), a 422 means the scope is larger than the server accepts. Everything
 * else stays deliberately generic — this says what the USER did, never what
 * the server did internally.
 */
export function sendFailureMessage(status: number, mentionCount: number): string {
  if (mentionCount === 0) return "Request failed.";
  if (status === 400) {
    return "A mentioned paper is no longer in this project. Remove the mention and send again.";
  }
  if (status === 422) {
    return `This message scopes to ${mentionCount} papers, which is more than allowed. Remove some mentions and send again.`;
  }
  return "Request failed.";
}

export function ChatStream({
  projectId,
  conversationId,
  initialMessages,
  onDone,
  onError,
  pendingContent,
  pendingMentions,
  papers,
}: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [streamingText, setStreamingText] = useState("");
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [retrievingInfo, setRetrievingInfo] = useState<RetrievingInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Re-seed messages if initialMessages prop changes (navigating between convs)
  useEffect(() => {
    setMessages(initialMessages);
    setStreamingText("");
    setStatus("idle");
    setError(null);
    // Bound the citation chunk cache to a single conversation view: a chunk
    // fetched under a stale chunk_index (paper re-ingested after it was
    // cached) must not leak into a different conversation's citations.
    resetChunkCache();
  }, [conversationId]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText]);

  useEffect(() => {
    if (!pendingContent) return;
    // Start SSE stream for the pending message
    let cancelled = false;
    setStreamingText("");
    setStatus("thinking");
    // Stale scope from a PRIOR turn must not survive into this one — a badge
    // claiming a scope the current turn doesn't have is worse than no badge.
    setRetrievingInfo(null);
    setError(null);

    const controller = new AbortController();
    const url = chatMessagesUrl(projectId, conversationId);
    const uid = getDevUserId();
    // POST via fetch (EventSource doesn't support POST), then read as SSE
    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(uid ? { "X-Dev-User-Id": uid } : {}),
      },
      body: JSON.stringify({ content: pendingContent, mentioned_paper_ids: pendingMentions ?? [] }),
      signal: controller.signal,
    }).then(async (res) => {
      if (!res.ok || !res.body) {
        const msg = sendFailureMessage(res.status, pendingMentions?.length ?? 0);
        setError(msg);
        setStatus("idle");
        onError?.(msg);
        onDone?.([]);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (!cancelled) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split(/\r\n\r\n|\n\n/);
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const eventLine = part.match(/^event: ([^\r\n]+)/m)?.[1];
          const dataLine = part.match(/^data: ([^\r\n]+)/m)?.[1];
          if (!eventLine || !dataLine) continue;
          try {
            const payload = JSON.parse(dataLine) as object;
            const ev = { type: eventLine, ...payload } as ChatEvent;
            if (ev.type === "thinking") {
              setStatus("thinking");
            } else if (ev.type === "retrieving") {
              setStatus("retrieving");
              setRetrievingInfo({
                paper_count: ev.paper_count,
                history_hits: ev.history_hits,
                scoped: ev.scoped,
                scoped_count: ev.scoped_count,
                widened: ev.widened,
                empty_mentions: ev.empty_mentions ?? [],
                scope_source: ev.scope_source ?? "mention",
                scope_evidence: ev.scope_evidence ?? [],
              });
            } else if (ev.type === "delta") {
              setStatus("streaming");
              setStreamingText((prev) => prev + ev.text);
            } else if (ev.type === "done") {
              setStatus("idle");
              setStreamingText("");
              // Refresh messages from snapshot
              getConversation(projectId, conversationId)
                .then((detail) => setMessages(detail.messages))
                .catch(() => {});
              onDone?.(ev.citations);
            } else if (ev.type === "error") {
              setError(ev.message);
              setStatus("idle");
              onError?.(ev.message);
              onDone?.([]);
            }
          } catch {
            // ignore malformed SSE
          }
        }
      }
    }).catch(() => {
      if (!cancelled) {
        const msg = "Connection error. Please try again.";
        setError(msg);
        setStatus("idle");
        onError?.(msg);
        onDone?.([]);
      }
    });

    return () => { cancelled = true; controller.abort(); };
  }, [pendingContent, pendingMentions, projectId, conversationId]);

  // Turns, not messages: a question and the answer it got are one unit of
  // reading, which a flat list cannot locate. `groupTurns` is pure and
  // tested; see lib/conversations.ts.
  const turns = groupTurns(messages);

  // The question currently in flight, or null. Written as a value rather than
  // a boolean so the JSX below narrows it: same condition as before
  // (`pendingContent && status !== "idle"`), it just carries the string.
  const live = pendingContent && status !== "idle" ? pendingContent : null;
  const scope = live ? scopeLine(retrievingInfo) : null;
  const emptyNote = live ? emptyMentionsNote(retrievingInfo) : null;
  const working = live ? statusLabel(status, retrievingInfo) : null;

  return (
    <div className="space-y-7">
      {turns.map((turn) => (
        <article key={turn.key} className="space-y-4">
          {turn.question && (
            <UserTurn
              content={turn.question.content}
              mentions={turn.question.mentions}
              papers={papers}
            />
          )}
          {turn.answers.map((answer) => (
            <AssistantAnswer
              key={answer.id}
              message={answer}
              question={turn.question?.content ?? ""}
              projectId={projectId}
            />
          ))}
        </article>
      ))}

      {live !== null && (
        <article className="space-y-4">
          {/* Optimistic user bubble. The mentions are ids the composer just
              handed over, so the same resolve-on-render rule applies. */}
          <UserTurn content={live} mentions={pendingMentions ?? []} papers={papers} />

          {/* The scope line survives into streaming on purpose: on a resolved
              scope the user clicked nothing, and this is the only place they
              learn the search was narrowed. */}
          {scope && <ScopeBanner segments={scope} note={emptyNote} />}

          {working && <StatusLine label={working} />}

          {streamingText && <StreamingAnswer text={streamingText} />}
        </article>
      )}

      {error && (
        <p role="alert" className="border-l-2 border-destructive pl-3 text-[13px] text-destructive">
          {error}
        </p>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
