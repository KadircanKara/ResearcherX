"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatCitation, ChatEvent, ChatMessage } from "@/lib/types";
import { chatMessagesUrl, getConversation } from "@/lib/chat";
import { getDevUserId } from "@/lib/api";
import { CitationHoverCard, queryTermsFrom } from "@/components/citation-hover-card";

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
}

export function ChatStream({
  projectId,
  conversationId,
  initialMessages,
  onDone,
  onError,
  pendingContent,
}: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [streamingText, setStreamingText] = useState("");
  const [status, setStatus] = useState<"idle" | "thinking" | "retrieving" | "streaming">("idle");
  const [retrievingInfo, setRetrievingInfo] = useState<{ paper_count: number; history_hits: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Re-seed messages if initialMessages prop changes (navigating between convs)
  useEffect(() => {
    setMessages(initialMessages);
    setStreamingText("");
    setStatus("idle");
    setError(null);
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
      body: JSON.stringify({ content: pendingContent }),
      signal: controller.signal,
    }).then(async (res) => {
      if (!res.ok || !res.body) {
        const msg = "Request failed.";
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
              setRetrievingInfo({ paper_count: ev.paper_count, history_hits: ev.history_hits });
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
  }, [pendingContent, projectId, conversationId]);

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
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.content}
                </ReactMarkdown>
              </div>
            ) : (
              <p>{msg.content}</p>
            )}
            {msg.role === "assistant" && msg.citations.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {msg.citations.map((c) => (
                  <CitationHoverCard
                    key={c.n}
                    citation={c}
                    projectId={projectId}
                    queryTerms={queryTermsFrom(
                      // The user message this answer replied to. The card stays
                      // presentational; resolving conversation state is this
                      // component's job, not its child's.
                      messages
                        .slice(0, messages.indexOf(msg))
                        .reverse()
                        .find((m) => m.role === "user")?.content ?? ""
                    )}
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

      {/* Streaming assistant bubble */}
      {streamingText && (
        <div className="flex justify-start">
          <div className="max-w-[80%] rounded-2xl bg-muted px-4 py-3 text-sm text-foreground">
            <div className={PROSE}>
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
