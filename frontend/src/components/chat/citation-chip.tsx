"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { formatChunkLocator } from "@/lib/citation-locator";
import { highlightTerms } from "@/lib/highlight-terms";
import { getPaperChunk } from "@/lib/projects";
import type { ChatCitation } from "@/lib/types";

// Module-level so it survives re-renders and is shared across every citation
// in the conversation — re-hovering the same source costs nothing.
//
// The key (`paper_id:chunk_index`) is unique across papers but NOT across
// time: `index_chunks` deletes and reinserts every row on re-index, so a
// given chunk_index can point at different text after a paper is
// re-ingested. The real protection against serving that stale text is the
// `startsWith(citation.snippet)` identity check applied at every read of
// this cache (initial state, the cache hit, and the fetch response) —
// `citation.snippet` is `chunk.text[:200]` persisted at citation time, so it
// is an exact content fingerprint of the chunk this citation actually pointed
// at. `resetChunkCache` is a secondary bound: it caps staleness to a single
// conversation view and closes an ordering wrinkle where a child's `useState`
// initializer can read this module cache during render before the parent's
// reset effect (on conversation change) has run. Call it whenever the
// conversation being displayed changes.
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

/**
 * One `[n]` marker in an answer, drawn as the prototype's superscript chip,
 * with the excerpt it stands for in a hover card.
 *
 * The card opens with the stored snippet and swaps in the chunk's full text
 * once it is fetched. The passage scrolls inside the card rather than growing
 * it: a real chunk runs to ~2,400 characters, which at the prototype's card
 * width would be taller than the screen.
 */
export function CitationChip({
  citation,
  projectId,
  queryTerms,
}: {
  citation: ChatCitation;
  projectId: string;
  queryTerms: string[];
}) {
  const key = `${citation.paper_id}:${citation.chunk_index}`;
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

  // Read from the CITATION, never from the fetched chunk. A citation's
  // section/page are snapshots of where the excerpt sat when this answer was
  // written, exactly like chunk_index and snippet; the chunk's own values
  // describe the paper as indexed today. It also keeps this line stable from
  // first paint and identical on a cache hit and a cache miss. "" for a
  // citation written before structured chunking, which renders nothing.
  const locator = formatChunkLocator(citation.section, citation.page);

  return (
    <HoverCard
      openDelay={100}
      closeDelay={80}
      onOpenChange={(open) => {
        if (open) void loadFullText();
      }}
    >
      <HoverCardTrigger
        render={
          <button
            type="button"
            className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-primary/15 px-1 align-super text-[10px] font-medium leading-none text-primary hover:bg-primary/25 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            aria-label={`Citation ${citation.n}, ${citation.title}`}
          />
        }
      >
        {citation.n}
      </HoverCardTrigger>
      <HoverCardContent>
        <div className="space-y-1.5 text-sm">
          <p className="font-medium leading-snug text-popover-foreground">{citation.title}</p>
          {locator ? <p className="text-xs text-muted-foreground">{locator}</p> : null}
          {/* Tuple form, not highlightTerms({...}): unified treats a bare
              function as an ATTACHER and calls it with the options, using its
              return value as the transformer. Passing an already-invoked
              transformer makes unified call it again with no arguments, and
              it crashes on an undefined tree.

              No rehype-raw and no citation plugin here. This text is
              PDF-derived, so raw HTML stays escaped; and paper text is full of
              [16]-style bibliography references, which the citation plugin
              would turn into citations of our own sources. */}
          <div className="max-h-60 space-y-1.5 overflow-y-auto text-xs leading-relaxed text-muted-foreground">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[[highlightTerms, { terms: queryTerms }]]}
              // remark-gfm autolinks bare URLs in paper text. Without
              // target="_blank" a click navigates the tab away from the
              // conversation, and rel="noopener noreferrer" keeps
              // window.opener from a PDF-derived link.
              components={{
                a: ({ node, ...props }) => {
                  void node;
                  return <a {...props} target="_blank" rel="noopener noreferrer" className="underline" />;
                },
              }}
            >
              {text}
            </ReactMarkdown>
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}
