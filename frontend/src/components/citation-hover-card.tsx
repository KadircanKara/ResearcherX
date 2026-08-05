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
// re-ingested. The real protection against serving that stale text is the
// `startsWith(citation.snippet)` identity check applied at every read of
// this cache (initial state, the in-flight cache hit, and the fetch
// response) — `citation.snippet` is `chunk.text[:200]` persisted at citation
// time, so it is an exact content fingerprint of the chunk this citation
// actually pointed at. `resetChunkCache` is a secondary belt-and-braces
// bound: it caps staleness to a single conversation view and closes an
// ordering wrinkle where a child's `useState` initializer can read this
// module cache during render before the parent's reset effect (on
// conversation change) has run. Call it whenever the conversation being
// displayed changes.
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
  // \b word boundaries so "search" doesn't light up inside "researcher".
  // \b matches at any word/non-word transition, so it still holds next to
  // punctuation — "reward," and "(reward)" both keep "reward" highlighted.
  const re = new RegExp(`\\b(${escaped.join("|")})\\b`, "gi");
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
  // Guard the cache read too: a value cached under this key may have been
  // written for a different (pre-re-index) chunk that happened to share the
  // same paper_id:chunk_index address.
  const [text, setText] = useState<string>(() => {
    const cached = chunkCache.get(key);
    return cached?.startsWith(citation.snippet) ? cached : citation.snippet;
  });

  async function loadFullText() {
    const cached = chunkCache.get(key);
    if (cached !== undefined) {
      if (cached.startsWith(citation.snippet)) setText(cached);
      return;
    }
    try {
      const chunk = await getPaperChunk(projectId, citation.paper_id, citation.chunk_index);
      // chunk_index is positional: a re-ingest reassigns it. The persisted
      // snippet is this chunk's first 200 chars at citation time, so it is
      // an exact identity check — a mismatch means chunk_index now points at
      // different text and this response must not be installed.
      if (!chunk.text.startsWith(citation.snippet)) return; // keep the snippet
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
      {/* render={<span />}: this is not a link (the default `<a>` has no
          href) and, more importantly, an href-less `<a>` is not keyboard-
          focusable — tabIndex={0} is what actually puts it in the tab
          order; Base UI wires focus-open (useFocus) but not focusability
          itself. The `<span title>` this replaced exposed the snippet to
          assistive tech and long-press on touch for free; PreviewCard's
          hover uses `mouseOnly: true`, so focus is the only remaining path
          for keyboard and touch users to reach this content. */}
      <PreviewCard.Trigger
        render={<span />}
        tabIndex={0}
        className={cn(
          "cursor-help rounded bg-background/50 px-1.5 py-0.5 text-xs",
          "text-muted-foreground transition-colors hover:bg-background",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        )}
      >
        [{citation.n}]
      </PreviewCard.Trigger>
      <PreviewCard.Portal>
        <PreviewCard.Positioner side="top" sideOffset={6} className="isolate z-50">
          <PreviewCard.Popup
            className={cn(
              "z-50 w-96 overflow-hidden rounded-lg border border-border",
              "bg-popover text-popover-foreground shadow-lg",
              "data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95",
              "data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95"
            )}
          >
            {/* Bottom fade signals there's more to scroll — at ~2375 chars
                of text-xs the passage clips well past the fold, and a user
                judging whether a citation supports a claim needs to know
                the visible slice isn't the whole thing. mask-image fades
                opacity rather than blending a color, so it holds in both
                light and dark without a theme-specific overlay color. */}
            <div
              className="max-h-72 overflow-y-auto p-3"
              style={{
                maskImage: "linear-gradient(to bottom, black calc(100% - 1.5rem), transparent)",
                WebkitMaskImage:
                  "linear-gradient(to bottom, black calc(100% - 1.5rem), transparent)",
              }}
            >
              <p className="mb-1.5 text-xs font-medium text-foreground">
                {citation.title}
              </p>
              <p className="text-xs leading-relaxed text-muted-foreground">
                {highlight(text, queryTerms)}
              </p>
            </div>
          </PreviewCard.Popup>
        </PreviewCard.Positioner>
      </PreviewCard.Portal>
    </PreviewCard.Root>
  );
}
