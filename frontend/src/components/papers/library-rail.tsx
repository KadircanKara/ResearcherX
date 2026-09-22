"use client";

import { useState } from "react";
import { Upload } from "lucide-react";
import { plural } from "@/lib/format";
import type { LibrarySummary } from "@/lib/papers";
import { cn } from "@/lib/utils";

function RailLine({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

/**
 * The right-hand "This library" summary + add-paper drop zone.
 *
 * The three counts come from `summarize`, which counts only states it can
 * stand behind.
 */
export function LibraryRail({
  summary,
  onOpenAdd,
}: {
  summary: LibrarySummary;
  /** Files dropped on the zone go STRAIGHT to the dialog, unfiltered. */
  onOpenAdd: (files: File[]) => void;
}) {
  const [dragOver, setDragOver] = useState(false);

  return (
    <aside className="slide-in-rail w-full shrink-0 lg:w-80">
      <div className="rounded-lg border bg-card p-4">
        <h2 className="text-sm font-semibold">This library</h2>
        {summary.total === 0 ? (
          <p className="mt-3 text-[13px] text-muted-foreground">Nothing here yet</p>
        ) : (
          <>
            <p className="mt-2 text-2xl font-semibold tabular-nums">
              {plural(summary.total, "paper")}
            </p>
            <dl className="mt-3 space-y-1.5 text-[13px]">
              <RailLine label="Searchable" value={summary.searchable} />
              <RailLine label="Not checked yet" value={summary.unchecked} />
              <RailLine label="Needs your attention" value={summary.attention} />
            </dl>
          </>
        )}
      </div>
      <button
        type="button"
        onClick={() => onOpenAdd([])}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          // Handed STRAIGHT to the add dialog rather than filtered here: the
          // upload tab already drops non-PDFs and caps the batch, and says
          // so — a second copy of that rule here would drop files silently.
          onOpenAdd(Array.from(e.dataTransfer.files));
        }}
        className={cn(
          "mt-4 flex w-full flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-6 text-center transition-colors duration-150",
          dragOver ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
        )}
      >
        <Upload className="size-5 text-muted-foreground" aria-hidden />
        <span className="text-[13px] font-medium">Drop a PDF here to add it to the library</span>
        <span className="text-[12px] text-muted-foreground">
          Papers are read, split and embedded on arrival — usually under a minute for 20 pages.
        </span>
      </button>
    </aside>
  );
}
