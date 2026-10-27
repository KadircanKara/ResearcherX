"use client";

import { useEffect, useState } from "react";
import { highlightKeywords } from "@/lib/papers";
import { getPaperSummary } from "@/lib/projects";
import type { Paper, PaperSummary } from "@/lib/types";

/** Keywords shown while collapsed; papers can carry up to 120. */
const KEYWORDS_SHOWN = 24;

/**
 * The paper's summary and keywords, in the opened row.
 *
 * Fetched when the row opens, never with the list: the list would otherwise
 * carry ~1.4k characters of summary for every paper in the library. Nothing
 * renders for a paper with no summary record yet.
 */
export function PaperSummaryBlock({ paper }: { paper: Paper }) {
  const [record, setRecord] = useState<PaperSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [allKeywords, setAllKeywords] = useState(false);
  const hasRecord = paper.summary_status !== "none";

  useEffect(() => {
    if (!hasRecord) return;
    let live = true;
    setLoading(true);
    getPaperSummary(paper.project_id, paper.id)
      .then((r) => live && setRecord(r))
      .catch(() => live && setRecord(null))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [paper.project_id, paper.id, hasRecord]);

  if (!hasRecord) return null;
  if (loading) return <p className="text-muted-foreground">Loading the summary…</p>;
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
