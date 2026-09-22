"use client";

import { queryTermsFrom } from "@/components/chat/citation-chip";
import { MarkdownRenderer } from "@/components/chat/markdown-renderer";
import { ScopeBanner } from "@/components/chat/scope-banner";
import type { ScopeSegment } from "@/lib/chat-scope";
import type { ChatMessage } from "@/lib/types";

/**
 * A finished answer, drawn as the app prototype's: its markdown with
 * citation chips, the papers it cited, and — for a turn whose scope the user
 * never chose — the scope line it was written under.
 */
export function AssistantAnswer({
  message,
  question,
  projectId,
  scope,
}: {
  message: ChatMessage;
  /** The question this answered, for highlighting terms in the citation card. */
  question: string;
  projectId: string;
  /** Kept only for a resolved scope; see `isPersistentScope`. */
  scope?: ScopeSegment[] | null;
}) {
  // The server renumbers citations 1..N by first appearance, so `n` order is
  // the order the reader meets them in — the prototype's Sources order.
  const sources = [...message.citations].sort((a, b) => a.n - b.n);

  return (
    <div className="max-w-[85%] space-y-3">
      <MarkdownRenderer
        markdown={message.content}
        citations={message.citations}
        projectId={projectId}
        queryTerms={queryTermsFrom(question)}
      />
      {sources.length > 0 ? (
        <div className="space-y-1 border-t pt-2 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">Sources</p>
          <ol className="list-decimal space-y-0.5 pl-4">
            {sources.map((source) => (
              <li key={source.n}>{source.title}</li>
            ))}
          </ol>
        </div>
      ) : null}
      {scope ? <ScopeBanner segments={scope} /> : null}
    </div>
  );
}

/**
 * The answer as it arrives: the same frame and renderer as a finished one,
 * without citations — they land with the `done` event, so mid-stream there is
 * nothing to resolve a marker against.
 */
export function StreamingAnswer({ text, projectId }: { text: string; projectId: string }) {
  return (
    <div className="max-w-[85%] space-y-3">
      <MarkdownRenderer markdown={text} citations={[]} projectId={projectId} queryTerms={[]} />
    </div>
  );
}
