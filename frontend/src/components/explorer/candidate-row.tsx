"use client";

import { useId, useState } from "react";
import { cn } from "@/lib/utils";
import { formatDistance } from "@/lib/explorer";
import type { Candidate } from "@/lib/explorer-data";

/**
 * One candidate, hung under the paragraph that argues for it.
 *
 * Deliberately not a card: a hairline row at footnote weight, subordinate to
 * the sentence above it, expanding in place for the evidence. The expansion is
 * the only animated thing in this design.
 *
 * The heading control holds SPANS, never an `<h3>`/`<p>` — nesting block
 * content inside a button is invalid and was caught once already in this
 * design.
 */
export function CandidateRow({
  candidate,
  held,
  onAdd,
}: {
  candidate: Candidate;
  /** True once the paper is in the library — from the data, or from this session. */
  held: boolean;
  onAdd: (candidate: Candidate) => void;
}) {
  const panelId = useId();
  const [open, setOpen] = useState(false);

  return (
    <div className={cn("rx-cand", held && "rx-cand-have")}>
      <div className="rx-cand-row">
        <button
          type="button"
          className="rx-cand-head"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((o) => !o)}
        >
          <span className="rx-cand-t">{candidate.title}</span>
          <span className="rx-cand-by">{candidate.byline}</span>
        </button>

        <span className="rx-cand-d">
          {candidate.distance === null ? (
            formatDistance(null)
          ) : (
            <>
              <b>{formatDistance(candidate.distance)}</b>to your library
            </>
          )}
        </span>

        {held || !candidate.action ? (
          <span className="rx-have-note">In your library</span>
        ) : (
          <button
            type="button"
            className={cn("rx-btn", candidate.action === "add-anyway" && "rx-btn-ghost")}
            onClick={() => onAdd(candidate)}
          >
            {candidate.action === "add-anyway" ? "Add anyway" : "Add to library"}
          </button>
        )}
      </div>

      <div id={panelId} className={cn("rx-reveal", open && "rx-reveal-open")}>
        <div>
          <div className="rx-reveal-body">
            {candidate.evidence.map((span, i) =>
              span.strong ? <b key={i}>{span.text}</b> : <span key={i}>{span.text}</span>
            )}
            <div className="rx-terms">
              {candidate.terms.map((t) => (
                <span
                  key={t.term}
                  className={cn(
                    "rx-chip",
                    t.kind === "hit" ? "rx-chip-hit" : "rx-chip-miss"
                  )}
                >
                  {t.term}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
