"use client";

import { memo, useEffect, useState } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import { PaperRowBody } from "@/components/papers/paper-row-body";
import { PaperStateDot } from "@/components/papers/paper-state-dot";
import { formatDate } from "@/lib/format";
import { indexCell, paperState, sourceLine, summaryCell } from "@/lib/papers";
import type { Paper } from "@/lib/types";

/** The open/close transition, `duration-200` on the body's grid. */
const CLOSE_MS = 200;

/**
 * One library row, closed or opened. The grid is the table header's grid.
 *
 * In edit mode the whole row selects rather than opens: the checkbox is a
 * 16px target, and the row is the thing the reader is pointing at.
 *
 * Memoized, and every callback takes the paper, so the table can hand the
 * page's stable callbacks straight through: a library holds two thousand
 * rows, and toggling one must re-render only the rows whose props changed.
 * `content-visibility: auto` lets the browser skip off-screen rows when the
 * opening row's height animates and everything below it moves.
 */
export const PaperRow = memo(function PaperRow({
  paper,
  editing,
  selected,
  onToggleSelect,
  isOpen,
  onToggleOpen,
  downloading,
  deleting,
  onRename,
  onRemove,
  onDownload,
}: {
  paper: Paper;
  editing: boolean;
  selected: boolean;
  onToggleSelect: (paper: Paper) => void;
  isOpen: boolean;
  onToggleOpen: (paper: Paper) => void;
  downloading: boolean;
  deleting: boolean;
  onRename: (paper: Paper) => void;
  onRemove: (paper: Paper) => void;
  onDownload: (paper: Paper) => void | Promise<void>;
}) {
  const bodyId = `paper-body-${paper.id}`;
  const state = paperState(paper);

  // The body stays mounted until the close transition has run, so closing
  // animates instead of snapping shut. Mounting is set during render, not in
  // an effect, so the body is already there on the frame the open transition
  // starts. Unmounting runs on a timer, not `transitionend`: that event never
  // fires when no transition runs (reduced motion, a hidden tab), and the
  // body would then stay mounted for good.
  const [bodyMounted, setBodyMounted] = useState(isOpen);
  if (isOpen && !bodyMounted) setBodyMounted(true);
  useEffect(() => {
    if (isOpen || !bodyMounted) return;
    const t = setTimeout(() => setBodyMounted(false), CLOSE_MS + 50);
    return () => clearTimeout(t);
  }, [isOpen, bodyMounted]);

  return (
    <div className="border-b [contain-intrinsic-size:auto_64px] [content-visibility:auto] last:border-b-0">
      <div className="flex items-start gap-3 px-4 py-3">
        {editing && (
          <Checkbox
            className="mt-1"
            checked={selected}
            onCheckedChange={() => onToggleSelect(paper)}
            aria-label={`Select ${paper.title}`}
          />
        )}
        <button
          type="button"
          className="grid flex-1 grid-cols-1 gap-1 rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[1fr_96px_76px_104px_96px_104px_120px] md:items-center md:gap-4"
          onClick={() => (editing ? onToggleSelect(paper) : onToggleOpen(paper))}
          aria-expanded={isOpen}
          aria-controls={bodyId}
        >
          <div className="min-w-0">
            <p className="truncate text-[14px] font-medium">{paper.title}</p>
            <p className="mt-0.5 truncate text-[12px] text-muted-foreground">
              {sourceLine(paper)}
            </p>
          </div>
          <div className="text-[13px] text-muted-foreground">
            <span className="text-muted-foreground md:hidden">Added: </span>
            {formatDate(paper.created_at)}
          </div>
          <div className="text-[13px] tabular-nums text-muted-foreground">
            <span className="text-muted-foreground md:hidden"># Chunks: </span>
            {paper.chunk_count}
          </div>
          <div>
            <span className="text-muted-foreground md:hidden">Embeddings: </span>
            <PaperStateDot state={indexCell(paper.dense_status)} />
          </div>
          <div>
            <span className="text-muted-foreground md:hidden">Keyword: </span>
            <PaperStateDot state={indexCell(paper.keyword_status)} />
          </div>
          <div>
            <span className="text-muted-foreground md:hidden">Summary: </span>
            <PaperStateDot state={summaryCell(paper.summary_status)} />
          </div>
          <div>
            <span className="text-muted-foreground md:hidden">State: </span>
            <PaperStateDot state={state} />
          </div>
        </button>
      </div>
      {/* A 0fr → 1fr grid row, which animates a height nobody has to measure. */}
      <div
        id={bodyId}
        className="grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none"
        style={{ gridTemplateRows: isOpen ? "1fr" : "0fr" }}
        inert={!isOpen}
      >
        <div className="overflow-hidden">
          {(isOpen || bodyMounted) && (
            <PaperRowBody
              paper={paper}
              state={state}
              downloading={downloading}
              deleting={deleting}
              onRename={() => onRename(paper)}
              onRemove={() => onRemove(paper)}
              onDownload={() => void onDownload(paper)}
            />
          )}
        </div>
      </div>
    </div>
  );
});
