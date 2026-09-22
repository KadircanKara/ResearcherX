"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { PaperRowBody } from "@/components/papers/paper-row-body";
import { PaperStateDot } from "@/components/papers/paper-state-dot";
import { formatDate } from "@/lib/format";
import { retrieverLabel, sourceLine, type PaperState } from "@/lib/papers";
import type { Paper } from "@/lib/types";

/**
 * One library row, closed or opened. The grid is the table header's grid.
 *
 * In edit mode the whole row selects rather than opens: the checkbox is a
 * 16px target, and the row is the thing the reader is pointing at.
 */
export function PaperRow({
  paper,
  state,
  editing,
  selected,
  onToggleSelect,
  isOpen,
  onToggleOpen,
  downloading,
  deleting,
  onCheckAgain,
  onRename,
  onRemove,
  onDownload,
}: {
  paper: Paper;
  state: PaperState;
  editing: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  isOpen: boolean;
  onToggleOpen: () => void;
  downloading: boolean;
  deleting: boolean;
  onCheckAgain: () => void;
  onRename: () => void;
  onRemove: () => void;
  onDownload: () => void;
}) {
  const bodyId = `paper-body-${paper.id}`;

  return (
    <div className="border-b last:border-b-0">
      <div className="flex items-start gap-3 px-4 py-3">
        {editing && (
          <Checkbox
            className="mt-1"
            checked={selected}
            onCheckedChange={onToggleSelect}
            aria-label={`Select ${paper.title}`}
          />
        )}
        <button
          type="button"
          className="grid flex-1 grid-cols-1 gap-1 rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[1fr_110px_120px_150px] md:items-center md:gap-4"
          onClick={editing ? onToggleSelect : onToggleOpen}
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
          <div className="text-[13px] text-muted-foreground">
            <span className="text-muted-foreground md:hidden">Retriever: </span>
            {retrieverLabel(state)}
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
        className="grid transition-[grid-template-rows] duration-200 ease-out"
        style={{ gridTemplateRows: isOpen ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          {isOpen && (
            <PaperRowBody
              paper={paper}
              state={state}
              downloading={downloading}
              deleting={deleting}
              onCheckAgain={onCheckAgain}
              onRename={onRename}
              onRemove={onRemove}
              onDownload={onDownload}
            />
          )}
        </div>
      </div>
    </div>
  );
}
