"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { PaperRowBody } from "@/components/papers/paper-row-body";
import { PaperStateDot } from "@/components/papers/paper-state-dot";
import { formatAdded, retrieverLabel, sourceLine, type PaperState } from "@/lib/papers";
import type { Paper } from "@/lib/types";

/** One library row, closed or opened. The grid is the table's header grid. */
export function PaperRow({
  paper,
  state,
  editing,
  selected,
  open,
  canEdit,
  checking,
  downloading,
  deleting,
  onToggleSelect,
  onToggleOpen,
  onCheckAgain,
  onRename,
  onRemove,
  onDownload,
}: {
  paper: Paper;
  state: PaperState;
  editing: boolean;
  selected: boolean;
  open: boolean;
  canEdit: boolean;
  checking: boolean;
  downloading: boolean;
  deleting: boolean;
  onToggleSelect: () => void;
  onToggleOpen: () => void;
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
          onClick={onToggleOpen}
          aria-expanded={open}
          aria-controls={bodyId}
          className="grid flex-1 grid-cols-1 gap-1 rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[minmax(0,1fr)_110px_120px_150px] md:items-center md:gap-4"
        >
          <div className="min-w-0">
            <p className="truncate text-[14px] font-medium">{paper.title}</p>
            <p className="mt-0.5 truncate text-[12px] text-muted-foreground">
              {sourceLine(paper)}
            </p>
          </div>
          {/* Each cell names itself below the table's breakpoint, where the
              header row is gone and a bare date or a bare "—" says nothing. */}
          <div className="text-[13px] text-muted-foreground">
            <span className="md:hidden">Added: </span>
            {formatAdded(paper.created_at)}
          </div>
          <div className="text-[13px] text-muted-foreground">
            <span className="md:hidden">Retriever: </span>
            {retrieverLabel(state)}
          </div>
          <div>
            <span className="text-[13px] text-muted-foreground md:hidden">State: </span>
            <PaperStateDot state={state} />
          </div>
        </button>
      </div>

      {/* A 0fr → 1fr grid row, which animates a height nobody has to measure. */}
      <div
        id={bodyId}
        className="grid transition-[grid-template-rows] duration-200 ease-out"
        style={{ gridTemplateRows: open ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          {open && (
            <PaperRowBody
              paper={paper}
              state={state}
              canEdit={canEdit}
              checking={checking}
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
