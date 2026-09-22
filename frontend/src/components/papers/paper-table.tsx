"use client";

import { PaperRow } from "@/components/papers/paper-row";
import { paperState, type ProbeMap } from "@/lib/papers";
import type { Paper } from "@/lib/types";

/**
 * The library table.
 *
 * Four columns — Paper, Added, Retriever, State — because that is what the
 * API carries: `PaperOut` has no authors and no year, so the concept's
 * Authors/Year columns have no source and are not invented here.
 */
export function PaperTable({
  papers,
  probes,
  editing,
  selectedIds,
  openId,
  canEdit,
  downloadingId,
  deletingId,
  onToggleSelect,
  onToggleOpen,
  onCheckAgain,
  onRename,
  onRemove,
  onDownload,
}: {
  papers: Paper[];
  probes: ProbeMap;
  editing: boolean;
  selectedIds: ReadonlySet<string>;
  openId: string | null;
  canEdit: boolean;
  downloadingId: string | null;
  deletingId: string | null;
  onToggleSelect: (paper: Paper) => void;
  onToggleOpen: (paper: Paper) => void;
  onCheckAgain: (paper: Paper) => void;
  onRename: (paper: Paper) => void;
  onRemove: (paper: Paper) => void;
  onDownload: (paper: Paper) => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="hidden grid-cols-[minmax(0,1fr)_110px_120px_150px] gap-4 border-b bg-muted/40 px-4 py-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase md:grid">
        <div>Paper</div>
        <div>Added</div>
        <div>Retriever</div>
        <div>State</div>
      </div>
      <div>
        {papers.map((paper) => (
          <PaperRow
            key={paper.id}
            paper={paper}
            state={paperState(paper, probes[paper.id])}
            editing={editing}
            selected={selectedIds.has(paper.id)}
            open={openId === paper.id}
            canEdit={canEdit}
            checking={probes[paper.id] === "checking"}
            downloading={downloadingId === paper.id}
            deleting={deletingId === paper.id}
            onToggleSelect={() => onToggleSelect(paper)}
            onToggleOpen={() => onToggleOpen(paper)}
            onCheckAgain={() => onCheckAgain(paper)}
            onRename={() => onRename(paper)}
            onRemove={() => onRemove(paper)}
            onDownload={() => onDownload(paper)}
          />
        ))}
      </div>
    </div>
  );
}
