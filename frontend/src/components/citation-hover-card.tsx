"use client";

import { useState } from "react";
import { PreviewCard } from "@base-ui/react/preview-card";
import { getPaperChunk } from "@/lib/projects";
import type { ChatCitation } from "@/lib/types";
import { cn } from "@/lib/utils";

// Module-level so it survives re-renders and is shared across every citation
// in the conversation — re-hovering the same source costs nothing.
//
// The key (`paper_id:chunk_index`) is unique across papers but NOT across
// time: `index_chunks` deletes and reinserts every row on re-index, so a
// given chunk_index can point at different text after a paper is
// re-ingested. An unbounded cache would then serve stale text forever with
// no signal — worse than the 404 case, which visibly falls back to the
// snippet. `resetChunkCache` bounds staleness to a single conversation view;
// call it whenever the conversation being displayed changes.
const chunkCache = new Map<string, string>();

export function resetChunkCache() {
  chunkCache.clear();
}

const STOPWORDS = new Set([
  "what", "which", "does", "used", "from", "with", "that", "this",
  "they", "their", "about", "paper", "papers",
]);

/** Question tokens worth highlighting: 4+ chars, not stopwords, deduped. */
export function queryTermsFrom(question: string): string[] {
  const seen = new Set<string>();
  for (const raw of question.toLowerCase().split(/\W+/)) {
    if (raw.length >= 4 && !STOPWORDS.has(raw)) seen.add(raw);
  }
  return [...seen];
}

/** Split text on term matches and wrap hits in <mark>.
 *
 * Never dangerouslySetInnerHTML — this text is paper-derived. Same reasoning
 * as the rehype-raw prohibition on the report renderer.
 */
function highlight(text: string, terms: string[]) {
  if (terms.length === 0) return text;
  // Longest first so "connectivity" wins over "connect".
  const escaped = [...terms]
    .sort((a, b) => b.length - a.length)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  return text.split(re).map((part, i) =>
    terms.includes(part.toLowerCase()) ? (
      <mark key={i} className="rounded bg-amber-300/30 px-0.5 text-inherit">
        {part}
      </mark>
    ) : (
      part
    )
  );
}

export function CitationHoverCard({
  citation,
  projectId,
  queryTerms,
}: {
  citation: ChatCitation;
  projectId: string;
  queryTerms: string[];
}) {
  const key = `${citation.paper_id}:${citation.chunk_index}`;
  // Start from the snippet already in the payload so the card has content the
  // instant it opens — no spinner, no layout shift when the full text lands.
  const [text, setText] = useState<string>(() => chunkCache.get(key) ?? citation.snippet);

  async function loadFullText() {
    if (chunkCache.has(key)) {
      setText(chunkCache.get(key)!);
      return;
    }
    try {
      const chunk = await getPaperChunk(projectId, citation.paper_id, citation.chunk_index);
      chunkCache.set(key, chunk.text);
      setText(chunk.text);
    } catch {
      // Keep the snippet. A failed preview must never replace readable content
      // with an error — the chunk may simply be gone after a re-ingest, which
      // regenerates every chunk_index.
    }
  }

  return (
    <PreviewCard.Root onOpenChange={(open) => open && void loadFullText()}>
      <PreviewCard.Trigger
        className={cn(
          "cursor-help rounded bg-background/50 px-1.5 py-0.5 text-xs",
          "text-muted-foreground transition-colors hover:bg-background"
        )}
      >
        [{citation.n}]
      </PreviewCard.Trigger>
      <PreviewCard.Portal>
        <PreviewCard.Positioner side="top" sideOffset={6} className="isolate z-50">
          <PreviewCard.Popup
            className={cn(
              "z-50 max-h-72 w-96 overflow-y-auto rounded-lg border border-border",
              "bg-popover p-3 text-popover-foreground shadow-lg",
              "data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95",
              "data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95"
            )}
          >
            <p className="mb-1.5 text-xs font-medium text-foreground">
              {citation.title}
            </p>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {highlight(text, queryTerms)}
            </p>
          </PreviewCard.Popup>
        </PreviewCard.Positioner>
      </PreviewCard.Portal>
    </PreviewCard.Root>
  );
}
