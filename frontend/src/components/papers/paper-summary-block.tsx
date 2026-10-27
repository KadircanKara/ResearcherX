"use client";

import { useEffect, useState } from "react";
import { highlightKeywords } from "@/lib/papers";
import { getPaperSummary } from "@/lib/projects";
import type { Paper, PaperSummary } from "@/lib/types";

/** Keywords shown while collapsed; papers can carry up to 120. */
const KEYWORDS_SHOWN = 24;

/**
 * Summaries already fetched this session, keyed by paper id and summary
 * status, so a reopened row renders at full height on its first frame and a
 * paper whose summary moved on (pending to ready) is fetched again.
 */
const cache = new Map<string, PaperSummary | null>();
const cacheKey = (paper: Paper) => `${paper.id}:${paper.summary_status}`;

/**
 * The paper's summary and keywords, in the opened row.
 *
 * Fetched when the row opens, never with the list: the list would otherwise
 * carry ~1.4k characters of summary for every paper in the library. Nothing
 * renders for a paper with no summary record yet. While the first fetch is in
 * flight a placeholder holds roughly the summary's height, so the row's open
 * animation is not chasing a target that grows halfway through.
 */
export function PaperSummaryBlock({ paper }: { paper: Paper }) {
  const key = cacheKey(paper);
  const hasRecord = paper.summary_status !== "none";
  const [record, setRecord] = useState<PaperSummary | null>(() => cache.get(key) ?? null);
  const [loading, setLoading] = useState(() => hasRecord && !cache.has(key));
  const [allKeywords, setAllKeywords] = useState(false);

  useEffect(() => {
    if (!hasRecord || cache.has(key)) return;
    let live = true;
    setLoading(true);
    getPaperSummary(paper.project_id, paper.id)
      .then((r) => {
        cache.set(key, r);
        if (live) setRecord(r);
      })
      .catch(() => live && setRecord(null))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [paper.project_id, paper.id, hasRecord, key]);

  if (!hasRecord) return null;
  if (loading) return <SummaryPlaceholder />;
  if (!record || !record.summary) return null;

  const keywords = allKeywords ? record.keywords : record.keywords.slice(0, KEYWORDS_SHOWN);
  const overflow = record.keywords.length - KEYWORDS_SHOWN;

  return (
    <div className="space-y-2">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        Summary
        {record.source === "abstract"
          ? " · the abstract stands in"
          : record.summary_model
            ? ` · ${record.summary_model}`
            : ""}
      </p>
      <p className="leading-relaxed text-foreground">
        {highlightKeywords(record.summary, record.keywords).map((seg, i) =>
          seg.bold ? (
            <strong key={i} className="font-semibold">
              {seg.text}
            </strong>
          ) : (
            seg.text
          )
        )}
      </p>
      {keywords.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {keywords.map((k) => (
            <span
              key={k}
              className="rounded-md border bg-muted/40 px-1.5 py-0.5 text-[12px] text-muted-foreground"
            >
              {k}
            </span>
          ))}
          {overflow > 0 && (
            <button
              type="button"
              className="px-1 text-[12px] text-primary underline-offset-2 hover:underline"
              onClick={() => setAllKeywords((all) => !all)}
            >
              {allKeywords ? "Show less" : `+${overflow} more`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Label, about seven lines of summary and two rows of keyword chips. */
function SummaryPlaceholder() {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading the summary">
      <div className="h-[15px] w-40 animate-pulse rounded bg-muted" />
      <div className="space-y-[7px]">
        {[100, 98, 100, 96, 100, 99, 62].map((w, i) => (
          <div key={i} className="h-[14px] animate-pulse rounded bg-muted" style={{ width: `${w}%` }} />
        ))}
      </div>
      <div className="h-[54px] animate-pulse rounded bg-muted/60" />
    </div>
  );
}
