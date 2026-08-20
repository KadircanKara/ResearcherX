"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatCitation, ChatEvent, ChatMessage, Paper } from "@/lib/types";
import { chatMessagesUrl, getConversation } from "@/lib/chat";
import { getDevUserId } from "@/lib/api";
import { CitationHoverCard, queryTermsFrom, resetChunkCache } from "@/components/citation-hover-card";
import { citationMarks } from "@/lib/citation-marks";
import { groupTurns } from "@/lib/conversations";
import {
  emptyMentionsNote,
  scopeLine,
  statusLabel,
  type ChatStatus,
  type RetrievingInfo,
} from "@/lib/chat-scope";

// Markdown inside the answer is styled by `chat.css` on `.rx-answer` in the
// concept's serif measure, NOT by `@tailwindcss/typography`'s `prose`: that
// scale was tuned for a 14px sans bubble, and mixing the two would leave two
// stylesheets arguing over every element's font-size.
//
// A wide table is contained by CSS alone (`display:block; overflow-x:auto` on
// the table itself), not by a wrapper component — the same trick
// `citation-hover-card.tsx` already uses. A wrapper would mean a `components`
// override whose only job is to drop react-markdown's `node` prop.

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
          // Rendered only inside the filled user bubble: a tint mixed from the
          // page accent is blue-on-blue there and renders invisible, so
          // `.rx-mention` mixes from the bubble's OWN foreground instead. See
          // the rule in chat.css.
          <span key={i} className="rx-mention">
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

/** The concept's scope glyph: a magnifier, at the head of the scope line. */
function ScopeGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.4 10.4 14 14" />
    </svg>
  );
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

  // Turns, not messages: the reading treatment draws a hairline BETWEEN a
  // question-and-answer pair, which a flat list cannot locate. `groupTurns`
  // is pure and tested; see lib/conversations.ts.
  const turns = groupTurns(messages);

  // The question currently in flight, or null. Written as a value rather than
  // a boolean so the JSX below narrows it: same condition as before
  // (`pendingContent && status !== "idle"`), it just carries the string.
  const live = pendingContent && status !== "idle" ? pendingContent : null;
  const scope = live ? scopeLine(retrievingInfo) : null;
  const emptyNote = live ? emptyMentionsNote(retrievingInfo) : null;
  const working = live ? statusLabel(status, retrievingInfo) : null;

  function renderAnswer(msg: ChatMessage, question: string) {
    // The user message this answer replied to, for term highlighting.
    // Resolving conversation state is this component's job, not the card's.
    const queryTerms = queryTermsFrom(question);
    return (
      <div key={msg.id}>
        <div className="rx-answer">
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
                    queryTerms={queryTerms}
                    variant="inline"
                  />
                );
              },
            }}
          >
            {msg.content}
          </ReactMarkdown>
        </div>
        {msg.citations.length > 0 && (
          <div className="rx-srcs">
            <span className="rx-srcs-h">Sources</span>
            {msg.citations.map((c, i) => (
              <CitationHoverCard
                key={c.n}
                citations={msg.citations}
                startIndex={i}
                projectId={projectId}
                queryTerms={queryTerms}
                variant="chip"
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    // No width cap here: the page wraps this AND the composer in one
    // `.rx-chcol`, so the two cannot drift apart.
    <div>
      {turns.map((turn) => (
        <article className="rx-turn" key={turn.key}>
          {turn.question && (
            <div className="rx-user-row">
              <div className="rx-bub-user">
                <MentionedContent
                  content={turn.question.content}
                  mentions={turn.question.mentions}
                  papers={papers}
                />
              </div>
            </div>
          )}
          {turn.answers.map((answer) => renderAnswer(answer, turn.question?.content ?? ""))}
        </article>
      ))}

      {live !== null && (
        <article className="rx-turn">
          {/* Optimistic user bubble. The mentions are ids the composer just
              handed over, so the same resolve-on-render rule applies. */}
          <div className="rx-user-row">
            <div className="rx-bub-user">
              <MentionedContent
                content={live}
                mentions={pendingMentions ?? []}
                papers={papers}
              />
            </div>
          </div>

          {/* The scope line. Deliberately NOT folded into the working line,
              which is about the phase: on a RESOLVED scope the user clicked
              nothing, so this is the only place they learn the answer was
              written from part of the library, and it has to survive into
              streaming to be read at all. Every word of it comes from
              `lib/chat-scope.ts`, which reads only the backend's own
              `retrieving` event. */}
          {scope && (
            <div className="rx-scope">
              <ScopeGlyph />
              <div>
                {scope.map((segment, i) =>
                  segment.emphasis ? <em key={i}>{segment.text}</em> : <span key={i}>{segment.text}</span>
                )}
                {/* A paper the user NAMED that returned nothing. Kept visible
                    through streaming: the answer is being written from fewer
                    papers than were asked for. */}
                {emptyNote && <span className="rx-scope-warn">{emptyNote}</span>}
              </div>
            </div>
          )}

          {working && (
            <p className="rx-working" role="status">
              <span>{working}</span>
              <span className="rx-curdot" aria-hidden="true" />
            </p>
          )}

          {streamingText && (
            // `rx-live` is what puts the writing caret at the end of the last
            // block, from CSS — appending a caret element to the markdown
            // would put it on a line of its own.
            //
            // No citationMarks here: citations arrive with the `done` event,
            // so mid-stream there is nothing to resolve a marker against.
            <div className="rx-live">
              <div className="rx-answer">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
              </div>
            </div>
          )}
        </article>
      )}

      {error && <p className="rx-cherror">{error}</p>}

      <div ref={bottomRef} />
    </div>
  );
}
