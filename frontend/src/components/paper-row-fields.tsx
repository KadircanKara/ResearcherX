"use client";

import { X } from "lucide-react";
import { Input } from "@/components/ui/input";

export const MAX_BATCH = 20;
export const TITLE_MAX = 150;

// A hung request must never wedge the batch: without a bound, runBatch never
// resolves, `saving` never clears, and the busy-guard leaves the dialog
// permanently undismissable. Generous enough for a large PDF upload + ingest.
// Dev embedding runs on CPU in-container (~47s for a 67-chunk paper, measured),
// and long papers scale past that. 120s left almost no margin.
export const ITEM_TIMEOUT_MS = 300_000;

export function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer)) as Promise<T>;
}

export type ItemStatus =
  | "pending"
  | "extracting"
  | "ready"
  | "saving"
  | "done"
  | "failed";

export type BatchItem = {
  id: string;
  source: File | string;
  label: string;
  title: string;
  abstract: string | null;
  body: string | null;
  status: ItemStatus;
  error?: string;
};

const BADGE: Record<ItemStatus, { text: string; className: string }> = {
  pending: { text: "queued", className: "bg-muted text-muted-foreground" },
  extracting: { text: "extracting", className: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" },
  ready: { text: "ready", className: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" },
  saving: { text: "saving", className: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" },
  done: { text: "added", className: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" },
  failed: { text: "failed", className: "bg-destructive/10 text-destructive" },
};

export function PaperRow({
  item,
  onTitleChange,
  onRemove,
}: {
  item: BatchItem;
  onTitleChange: (title: string) => void;
  onRemove: () => void;
}) {
  const badge = BADGE[item.status];
  const busy = item.status === "extracting" || item.status === "saving";

  return (
    <div className="flex items-center gap-2 rounded-lg border border-border px-3 py-2">
      <span
        className="w-24 shrink-0 truncate font-mono text-[11px] text-muted-foreground"
        title={item.label}
      >
        {item.label}
      </span>
      <Input
        value={item.title}
        maxLength={TITLE_MAX}
        disabled={busy}
        onChange={(e) => onTitleChange(e.target.value)}
        className="h-8 flex-1 text-sm"
      />
      <span
        className={"shrink-0 rounded-full px-2 py-0.5 text-[10px] " + badge.className}
        title={item.error}
      >
        {badge.text}
      </span>
      <button
        type="button"
        onClick={onRemove}
        disabled={busy}
        aria-label={`Remove ${item.label}`}
        className="shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-30"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}
