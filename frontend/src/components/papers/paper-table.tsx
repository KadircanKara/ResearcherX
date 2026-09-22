"use client";

import { PaperRow } from "@/components/papers/paper-row";
import { paperState, type ProbeMap } from "@/lib/papers";
import type { Paper } from "@/lib/types";

/**
 * The library table.
 *
 * Four columns — Paper, Added, Retriever, State — because that is what the
 * API carries: `PaperOut` has no authors and no year.
 */
export function PaperTable({
  papers,
  probes,
  editing,
  selectedIds,
  onToggleSelect,
  openId,
  onToggleOpen,
  downloadingId,
  deletingId,
  onCheckAgain,
  onRename,
  onRemove,
  onDownload,
}: {
  papers: Paper[];
  probes: ProbeMap;
  editing: boolean;
  selectedIds: ReadonlySet<string>;
  onToggleSelect: (paper: Paper) => void;
  openId: string | null;
  onToggleOpen: (paper: Paper) => void;
  downloadingId: string | null;
  deletingId: string | null;
  onCheckAgain: (paper: Paper) => void;
  onRename: (paper: Paper) => void;
  onRemove: (paper: Paper) => void;
  onDownload: (paper: Paper) => void;
}) {
  return (
    <div className="rounded-lg border">
      <div className="hidden grid-cols-[1fr_110px_120px_150px] gap-4 border-b bg-muted/40 px-4 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground md:grid">
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
            onToggleSelect={() => onToggleSelect(paper)}
            isOpen={openId === paper.id}
            onToggleOpen={() => onToggleOpen(paper)}
            downloading={downloadingId === paper.id}
            deleting={deletingId === paper.id}
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
