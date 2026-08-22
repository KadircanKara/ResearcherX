"use client";

import { Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";

interface BulkEditBarProps {
  active: boolean;
  count: number;
  total: number;
  allSelected: boolean;
  busy: boolean;
  onEnter: () => void;
  onSelectAll: () => void;
  onClear: () => void;
  onDelete: () => void;
  onDone: () => void;
}

/**
 * Presentational only. Every list owns its own selection state -- the bar
 * renders it and reports intent, so the three lists cannot drift into three
 * different edit-mode behaviours.
 */
export function BulkEditBar({
  active,
  count,
  total,
  allSelected,
  busy,
  onEnter,
  onSelectAll,
  onClear,
  onDelete,
  onDone,
}: BulkEditBarProps) {
  if (!active) {
    return (
      <Button size="sm" variant="outline" disabled={total === 0} onClick={onEnter}>
        Edit
      </Button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">{count} selected</span>
      <Button
        size="sm"
        variant="ghost"
        onClick={allSelected ? onClear : onSelectAll}
        disabled={total === 0}
      >
        {allSelected ? "Clear" : "Select all"}
      </Button>
      <Button size="sm" variant="destructive" disabled={count === 0 || busy} onClick={onDelete}>
        {busy ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <Trash2 className="mr-1.5 size-3.5" />}
        Delete
      </Button>
      <Button size="sm" variant="outline" onClick={onDone} disabled={busy}>
        Done
      </Button>
    </div>
  );
}
