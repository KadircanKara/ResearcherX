"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatCitation, ChatEvent, ChatMessage, Paper } from "@/lib/types";
import { chatMessagesUrl, getConversation } from "@/lib/chat";
import { getDevUserId } from "@/lib/api";
import { CitationHoverCard, queryTermsFrom, resetChunkCache } from "@/components/citation-hover-card";
import { citationMarks } from "@/lib/citation-marks";

// Typography for markdown inside a chat bubble. `prose-invert` under .dark
// (tailwind darkMode: "class"). max-w-none because the bubble already caps
// width. The margin overrides matter: default prose spacing is tuned for
// article bodies and leaves a short answer swimming in padding inside a bubble.
const PROSE =
  "prose prose-sm dark:prose-invert max-w-none " +
  "prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5 " +
  "prose-headings:mt-3 prose-headings:mb-1.5 prose-pre:my-2 " +
  "prose-table:my-2 prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1 " +
  "first:prose-p:mt-0 last:prose-p:mb-0";

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

function MentionedContent({ content, mentions, papers }: {
  content: string;
  mentions: string[];
  papers: Paper[];
}) {
  if (mentions.length === 0) return <>{content}</>;
  // Each id is resolved to its CURRENT title at render time, then matched
  // against the message's frozen `content` string below. This does NOT make
  // a rename "relabel every past turn" the way citation chips do: `content`
  // is a historical record of what the user typed and is never rewritten,
  // so after a rename the text still holds the OLD title. The highlight
  // then simply stops matching (the mention keeps working for retrieval
  // SCOPE, which is id-based) rather than tracking the new name.
  const titles = mentions
    .map((id) => papers.find((p) => p.id === id)?.title)
    .filter((t): t is string => Boolean(t));
  if (titles.length === 0) return <>{content}</>;
  // Longest-first, same convention as reconcileMentions in lib/mentions.ts:
  // otherwise a shorter co-mentioned title that prefixes a longer one (e.g.
  // "RL" and "RL Survey") can steal the match and split the longer title in two.
  const sortedTitles = [...titles].sort((a, b) => b.length - a.length);
  const parts = content.split(new RegExp(`(${sortedTitles.map(escapeRegExp).map((t) => `@${t}`).join("|")})`));
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("@") && titles.some((t) => part === `@${t}`) ? (
          // Rendered only inside the user bubble (bg-primary text-primary-foreground):
          // bg-primary/10 + text-primary from the brief is blue-on-blue there and
          // renders invisible. primary-foreground/20 keeps contrast against bg-primary.
          <span key={i} className="rounded bg-primary-foreground/20 px-1 font-semibold text-primary-foreground">
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
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
  const [status, setStatus] = useState<"idle" | "thinking" | "retrieving" | "streaming">("idle");
  const [retrievingInfo, setRetrievingInfo] = useState<{
    paper_count: number;
    history_hits: number;
    scoped: boolean;
    scoped_count: number;
    widened: boolean;
    empty_mentions: string[];
    scope_source: "mention" | "resolved";
    scope_evidence: string[];
  } | null>(null);
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

  // The user message this answer replied to, for term highlighting. Resolving
  // conversation state is this component's job, not the card's.
  function questionFor(msg: ChatMessage): string[] {
    return queryTermsFrom(
      messages
        .slice(0, messages.indexOf(msg))
        .reverse()
        .find((m) => m.role === "user")?.content ?? ""
    );
  }

  return (
    <div className="flex flex-col gap-4 py-4">
      {messages.map((msg) => (
        <div
          key={msg.id}
          className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div
            className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
              msg.role === "user"
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-foreground"
            }`}
          >
            {msg.role === "assistant" ? (
              <div className={PROSE}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  // Tuple form, not citationMarks({...}): unified treats a bare
                  // function as an ATTACHER and calls it with the options, using
                  // its return value as the transformer. Passing an
                  // already-invoked transformer makes unified call it again with
                  // no arguments, and it crashes on an undefined tree — after
                  // passing tsc, lint and build, so only a browser check finds it.
                  rehypePlugins={[
                    [citationMarks, { valid: new Set(msg.citations.map((c) => c.n)) }],
                  ]}
                  components={{
                    span: ({ node, children, ...props }) => {
                      const raw = props as Record<string, string | undefined>;
                      const n = Number(raw["data-citation-n"]);
                      const groupAttr = raw["data-citation-group"];
                      if (!groupAttr || Number.isNaN(n)) return <span {...props}>{children}</span>;
                      const group = groupAttr
                        .split(",")
                        .map(Number)
                        .map((num) => msg.citations.find((c) => c.n === num))
                        .filter((c): c is ChatCitation => c !== undefined);
                      const start = group.findIndex((c) => c.n === n);
                      if (start === -1) return <span {...props}>{children}</span>;
                      return (
                        <CitationHoverCard
                          citations={group}
                          startIndex={start}
                          projectId={projectId}
                          queryTerms={questionFor(msg)}
                          variant="inline"
                        />
                      );
                    },
                  }}
                >
                  {msg.content}
                </ReactMarkdown>
              </div>
            ) : (
              <p>
                <MentionedContent content={msg.content} mentions={msg.mentions} papers={papers} />
              </p>
            )}
            {msg.role === "assistant" && msg.citations.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {msg.citations.map((c, i) => (
                  <CitationHoverCard
                    key={c.n}
                    citations={msg.citations}
                    startIndex={i}
                    projectId={projectId}
                    queryTerms={questionFor(msg)}
                    variant="chip"
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      ))}

      {/* Optimistic user bubble */}
      {pendingContent && status !== "idle" && (
        <div className="flex justify-end">
          <div className="max-w-[80%] rounded-2xl bg-primary px-4 py-3 text-sm text-primary-foreground">
            {pendingContent}
          </div>
        </div>
      )}

      {/* Resolved scope banner.
          Deliberately NOT inside the status indicator, which the streaming
          bubble replaces the moment the first token lands. The user picked
          nothing this turn — the words they typed narrowed the search — so
          this is the only place they can learn the answer was written from
          part of the library, and it has to outlive retrieval to be read at
          all. A mention scope needs no such banner: the chips are still in
          their own composer.
          It quotes their OWN phrase, not the paper title: the title is what
          we picked, the phrase is what they typed, and only the phrase tells
          them what to change to search wider. */}
      {status !== "idle" &&
        retrievingInfo?.scope_source === "resolved" &&
        retrievingInfo.scoped_count > 0 && (
          <div className="flex justify-start">
            <div className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground">
              {retrievingInfo.scope_evidence.length
                ? `matched ${retrievingInfo.scope_evidence
                    .map((e) => `“${e}”`)
                    .join(", ")} in your question — `
                : "your question named papers — "}
              {`searching ${retrievingInfo.scoped_count} paper${
                retrievingInfo.scoped_count === 1 ? "" : "s"
              }${retrievingInfo.widened ? " + the rest of the library" : " only"}`}
            </div>
          </div>
        )}

      {/* Streaming assistant bubble */}
      {streamingText && (
        <div className="flex justify-start">
          <div className="max-w-[80%] rounded-2xl bg-muted px-4 py-3 text-sm text-foreground">
            <div className={PROSE}>
              {/* No citationMarks here: citations arrive with the `done` event,
                  so mid-stream there is nothing to resolve a marker against. */}
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}

      {/* Status indicator */}
      {status !== "idle" && !streamingText && (
        <div className="flex justify-start">
          <div className="rounded-2xl bg-muted px-4 py-3 text-xs text-muted-foreground">
            {status === "thinking" && "Analyzing…"}
            {status === "retrieving" && (retrievingInfo
              ? `Retrieving from ${retrievingInfo.paper_count} paper${retrievingInfo.paper_count !== 1 ? "s" : ""}…`
              : "Retrieving…")}
            {/* A paper the user NAMED that returned nothing. Kept visible
                through streaming, not just the retrieval phase: the answer is
                being written from fewer papers than were asked for, and that
                is exactly when the reader needs to know. The durable record is
                the answer itself — the model is instructed to say so — since
                this flag is not persisted with the message. */}
            {(status === "retrieving" || status === "streaming") &&
              !!retrievingInfo?.empty_mentions?.length && (
                <span className="ml-2 text-xs text-amber-600 dark:text-amber-500">
                  no excerpts from {retrievingInfo.empty_mentions.join(", ")}
                </span>
              )}
            {/* Mention scope only. A RESOLVED scope gets its own banner
                above, which survives into streaming — this indicator does
                not. */}
            {status === "retrieving" &&
              retrievingInfo?.scoped &&
              retrievingInfo.scope_source !== "resolved" && (
                <span className="ml-2 text-xs text-muted-foreground">
                  {retrievingInfo.scoped_count === 0
                    ? // Papers were mentioned but none could be scoped to (all
                      // deleted, or retrieval could not run). The backend reports
                      // this widened; "scoped to 0 papers" would claim a scope
                      // that was never applied.
                      "mentions unavailable — searching the library"
                    : `scoped to ${retrievingInfo.scoped_count} paper${
                        retrievingInfo.scoped_count === 1 ? "" : "s"
                      }${retrievingInfo.widened ? " + library" : ""}`}
                </span>
              )}
          </div>
        </div>
      )}

      {error && (
        <p className="text-center text-xs text-destructive">{error}</p>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
