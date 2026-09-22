"use client";

import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

interface BulkEditBarProps {
  /** Whether the list is in edit mode. (`active` is the older name.) */
  editing?: boolean;
  active?: boolean;
  /** How many rows are selected. (`count` is the older name.) */
  selectedCount?: number;
  count?: number;
  /** Rows available to select; 0 disables Edit and Select all. Optional. */
  total?: number;
  /** Accepted for older callers; the prototype's bar shows both buttons. */
  allSelected?: boolean;
  /** A delete in flight: Delete shows a spinner and every button waits. */
  busy?: boolean;
  /** Enter edit mode. (`onEnter` is the older name.) */
  onStart?: () => void;
  onEnter?: () => void;
  onSelectAll: () => void;
  onClear: () => void;
  onDelete: () => void;
  onDone: () => void;
}

/**
 * The selection bar shared by the conversation list, the paper table and the
 * LaTeX project list, drawn as the prototype's.
 *
 * Presentational only. Every list owns its own selection state -- the bar
 * renders it and reports intent, so the three lists cannot drift into three
 * different edit-mode behaviours.
 */
export function BulkEditBar({
  editing,
  active,
  selectedCount,
  count,
  total,
  busy = false,
  onStart,
  onEnter,
  onSelectAll,
  onClear,
  onDelete,
  onDone,
}: BulkEditBarProps) {
  const isEditing = editing ?? active ?? false;
  const selected = selectedCount ?? count ?? 0;
  const empty = total === 0;

  if (!isEditing) {
    return (
      <Button variant="outline" size="sm" disabled={empty} onClick={onStart ?? onEnter}>
        Edit
      </Button>
    );
  }

  return (
    <div className="fade-block flex flex-wrap items-center gap-2">
      <span className="text-[13px] tabular-nums text-muted-foreground">{selected} selected</span>
      <Button variant="ghost" size="sm" disabled={empty || busy} onClick={onSelectAll}>
        Select all
      </Button>
      <Button variant="ghost" size="sm" disabled={busy} onClick={onClear}>
        Clear
      </Button>
      <Button
        variant="destructive"
        size="sm"
        disabled={selected === 0 || busy}
        onClick={onDelete}
      >
        {busy && <Loader2 className="animate-spin" />}
        Delete
      </Button>
      <Button variant="outline" size="sm" disabled={busy} onClick={onDone}>
        Done
      </Button>
    </div>
  );
}
