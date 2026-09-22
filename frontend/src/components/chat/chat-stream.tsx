"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatCitation, ChatEvent, ChatMessage, Paper } from "@/lib/types";
import { chatMessagesUrl, getConversation } from "@/lib/chat";
import { getDevUserId } from "@/lib/api";
import { AssistantAnswer } from "@/components/chat/assistant-answer";
import { resetChunkCache } from "@/components/chat/citation-chip";
import { StreamingTurn } from "@/components/chat/streaming-turn";
import { UserTurn } from "@/components/chat/user-turn";
import { groupTurns } from "@/lib/conversations";
import {
  emptyMentionsNote,
  isPersistentScope,
  scopeLine,
  statusLabel,
  type ChatStatus,
  type RetrievingInfo,
  type ScopeSegment,
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

/**
 * The thread's turns, laid out as the app prototype's: one flat column of
 * question bubbles and answers, the turn in flight at the end of it, and a
 * failure line under the column.
 *
 * Renders a FRAGMENT — the column and the failure line — so both sit directly
 * in the page's own vertical rhythm, between the header and the composer,
 * exactly where the prototype puts them.
 */
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
  // The scope line of a turn answered in THIS view, by assistant message id,
  // kept only when `isPersistentScope` says it must outlive the turn. The
  // scope is not persisted with the message, so a reload loses it; the
  // durable record is the answer, which the model is told to qualify.
  const [keptScopes, setKeptScopes] = useState<Record<string, ScopeSegment[]>>({});
  const bottomRef = useRef<HTMLDivElement>(null);

  // Re-seed messages if initialMessages prop changes (navigating between convs)
  useEffect(() => {
    setMessages(initialMessages);
    setStreamingText("");
    setStatus("idle");
    setError(null);
    setKeptScopes({});
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
    // The retrieving payload as this stream saw it. A local, not the state
    // above: the `done` branch runs inside this closure, where the state
    // value is still the one captured when the effect started.
    let info: RetrievingInfo | null = null;
    setStreamingText("");
    setStatus("thinking");
    // Stale scope from a PRIOR turn must not survive into this one — a badge
    // claiming a scope the current turn doesn't have is worse than no badge.
    setRetrievingInfo(null);
    setError(null);

    // The answer is finished: swap the streamed draft for the stored message.
    // The live turn stays on screen until the refreshed messages are in hand
    // and both change in ONE update — clearing it first left the question and
    // its answer missing from the thread for the length of the refetch.
    async function settle(citations: ChatCitation[]) {
      let detail = null;
      try {
        detail = await getConversation(projectId, conversationId);
      } catch {
        // Keep the snapshot we have; the next load shows the answer.
      }
      if (cancelled) return;
      if (detail) {
        setMessages(detail.messages);
        const answer = [...detail.messages].reverse().find((m) => m.role === "assistant");
        const kept = isPersistentScope(info) ? scopeLine(info) : null;
        if (answer && kept) setKeptScopes((prev) => ({ ...prev, [answer.id]: kept }));
      }
      setStatus("idle");
      setStreamingText("");
      onDone?.(citations);
    }

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
              info = {
                paper_count: ev.paper_count,
                history_hits: ev.history_hits,
                scoped: ev.scoped,
                scoped_count: ev.scoped_count,
                widened: ev.widened,
                empty_mentions: ev.empty_mentions ?? [],
                scope_source: ev.scope_source ?? "mention",
                scope_evidence: ev.scope_evidence ?? [],
              };
              setStatus("retrieving");
              setRetrievingInfo(info);
            } else if (ev.type === "delta") {
              setStatus("streaming");
              setStreamingText((prev) => prev + ev.text);
            } else if (ev.type === "done") {
              void settle(ev.citations);
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

  // Each answer is handed the question it answered, for highlighting that
  // question's terms in its citation cards; see `groupTurns`.
  const turns = groupTurns(messages);

  // The question currently in flight, or null. Written as a value rather than
  // a boolean so the JSX below narrows it.
  const live = pendingContent && status !== "idle" ? pendingContent : null;

  return (
    <>
      <div className="space-y-7">
        {turns.map((turn) => [
          turn.question ? (
            <UserTurn
              key={turn.question.id}
              content={turn.question.content}
              mentions={turn.question.mentions}
              papers={papers}
            />
          ) : null,
          ...turn.answers.map((answer) => (
            <AssistantAnswer
              key={answer.id}
              message={answer}
              question={turn.question?.content ?? ""}
              projectId={projectId}
              scope={keptScopes[answer.id] ?? null}
            />
          )),
        ])}

        {live !== null && (
          // Optimistic user bubble. The mentions are ids the composer just
          // handed over, so the same resolve-on-render rule applies.
          <UserTurn content={live} mentions={pendingMentions ?? []} papers={papers} />
        )}
        {live !== null && (
          <StreamingTurn
            status={status}
            label={statusLabel(status, retrievingInfo)}
            scope={scopeLine(retrievingInfo)}
            note={emptyMentionsNote(retrievingInfo)}
            text={streamingText}
            projectId={projectId}
          />
        )}

        {/* Scroll target. Zero height and no margin, so it adds nothing to
            the column's spacing. */}
        <div ref={bottomRef} aria-hidden className="!mt-0" />
      </div>

      {error && (
        <p role="alert" className="border-l-2 border-destructive pl-3 text-[13px] text-destructive">
          {error}
        </p>
      )}
    </>
  );
}
