"use client";

import { useEffect, useRef, useState } from "react";
import { PreviewCard } from "@base-ui/react/preview-card";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getPaperChunk } from "@/lib/projects";
import { highlightTerms } from "@/lib/highlight-terms";
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

// Markdown styling for a 24rem card at text-xs. Deliberately not the answer
// bubble's PROSE constant, which is tuned for a much wider container: a paper's
// "## B. Reward Function" heading rendered at prose defaults would dominate the
// card. Headings are clamped near body size and every margin is tightened.
const CARD_PROSE =
  "prose prose-sm dark:prose-invert max-w-none text-xs " +
  "prose-p:my-1 prose-p:text-xs prose-li:my-0 prose-li:text-xs " +
  "prose-ul:my-1 prose-ol:my-1 " +
  "prose-headings:text-xs prose-headings:font-semibold prose-headings:mt-2 prose-headings:mb-1 " +
  "prose-code:text-[11px] prose-pre:text-[11px] prose-pre:my-1 " +
  "prose-table:my-1 prose-table:text-[11px] prose-th:px-1 prose-th:py-0.5 " +
  "prose-td:px-1 prose-td:py-0.5 " +
  "first:prose-p:mt-0 last:prose-p:mb-0";

/** Question tokens worth highlighting: 4+ chars, not stopwords, deduped. */
export function queryTermsFrom(question: string): string[] {
  const seen = new Set<string>();
  for (const raw of question.toLowerCase().split(/\W+/)) {
    if (raw.length >= 4 && !STOPWORDS.has(raw)) seen.add(raw);
  }
  return [...seen];
}

export function CitationHoverCard({
  citations,
  startIndex,
  projectId,
  queryTerms,
  variant,
}: {
  citations: ChatCitation[];
  startIndex: number;
  projectId: string;
  queryTerms: string[];
  variant: "inline" | "chip";
}) {
  const [index, setIndex] = useState(startIndex);
  // What this trigger IS, fixed for the life of the marker. Distinct from
  // `citation` below, which follows navigation: rendering the navigated
  // citation here rewrote the marker in the answer text, turning
  // "[7], [8]" into "[8], [8]" after one press of the arrow.
  const anchor = citations[startIndex] ?? citations[0];
  const citation = citations[index] ?? citations[0];
  const key = `${citation.paper_id}:${citation.chunk_index}`;
  const [text, setText] = useState<string>(() => {
    const cached = chunkCache.get(key);
    return cached?.startsWith(citation.snippet) ? cached : citation.snippet;
  });

  // Which citation the card is currently showing. A fetch started for one
  // sibling must not install its result after the reader has stepped to
  // another: the snippet guard below validates a response against ITS OWN
  // citation, so a late-landing response passes its own check and would
  // overwrite a newer sibling's passage — right title, wrong text.
  const shownKey = useRef(key);
  shownKey.current = key;

  // Navigating changes which citation is displayed, so the passage must
  // follow it. Seed from cache-or-snippet first — same guard as the initial
  // state — so the card never blanks between siblings, then fetch.
  useEffect(() => {
    const cached = chunkCache.get(key);
    setText(cached?.startsWith(citation.snippet) ? cached : citation.snippet);
    void loadFullText(key);
    // loadFullText is redefined every render and closes over `citation`;
    // keying the effect on `key` is what makes it run once per shown citation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  async function loadFullText(forKey: string) {
    const cached = chunkCache.get(key);
    if (cached !== undefined) {
      // shownKey check: a sibling fetch's cache hit resolves synchronously,
      // but by the time an AWAITed one gets here the reader may have already
      // stepped again — same race as the network branch below.
      if (cached.startsWith(citation.snippet) && shownKey.current === forKey) setText(cached);
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
      // Separate from the snippet check above, which only catches a
      // re-indexed chunk: this catches a late response for a citation the
      // reader has since navigated away from. The cache write above still
      // stands — the text is valid, just no longer what THIS card should
      // show — only the setText is guarded.
      if (shownKey.current !== forKey) return;
      setText(chunk.text);
    } catch {
      // Keep the snippet. A failed preview must never replace readable content
      // with an error — the chunk may simply be gone after a re-ingest, which
      // regenerates every chunk_index.
    }
  }

  const hasGroup = citations.length > 1;
  function step(delta: number) {
    setIndex((i) => Math.min(citations.length - 1, Math.max(0, i + delta)));
  }

  return (
    <PreviewCard.Root
      onOpenChange={(open) => {
        if (open) void loadFullText(key);
        else setIndex(startIndex);
      }}
    >
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
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); }
          if (event.key === "ArrowRight") { event.preventDefault(); step(1); }
        }}
        className={cn(
          "cursor-help rounded transition-colors",
          "text-blue-600 dark:text-blue-400",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          variant === "chip"
            ? "bg-background/50 px-1.5 py-0.5 text-xs hover:bg-background"
            : "font-medium hover:underline"
        )}
      >
        [{anchor.n}]
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
              {/* Tuple form, not highlightTerms({...}): unified treats a bare
                  function as an ATTACHER and calls it with the options, using
                  its return value as the transformer. Passing an
                  already-invoked transformer makes unified call it again with
                  no arguments, and it crashes on an undefined tree.

                  No rehype-raw and no citationMarks here. This text is
                  PDF-derived, so raw HTML stays escaped; and paper text is full
                  of [16]-style bibliography references, which the citation
                  plugin would turn into clickable citations of our own
                  sources. */}
              <div className={cn(CARD_PROSE, "text-muted-foreground [&_table]:block [&_table]:overflow-x-auto")}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[[highlightTerms, { terms: queryTerms }]]}
                >
                  {text}
                </ReactMarkdown>
              </div>
            </div>
            {hasGroup && (
              <div className="flex items-center justify-between border-t border-border px-3 py-1.5">
                <button
                  type="button"
                  aria-label="Previous citation"
                  disabled={index === 0}
                  onClick={() => step(-1)}
                  className="rounded px-1 text-xs text-muted-foreground disabled:opacity-30 hover:text-foreground"
                >
                  ◀
                </button>
                <span aria-live="polite" className="text-[11px] text-muted-foreground">
                  {index + 1} of {citations.length}
                </span>
                <button
                  type="button"
                  aria-label="Next citation"
                  disabled={index === citations.length - 1}
                  onClick={() => step(1)}
                  className="rounded px-1 text-xs text-muted-foreground disabled:opacity-30 hover:text-foreground"
                >
                  ▶
                </button>
              </div>
            )}
          </PreviewCard.Popup>
        </PreviewCard.Positioner>
      </PreviewCard.Portal>
    </PreviewCard.Root>
  );
}
