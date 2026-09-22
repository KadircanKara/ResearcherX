"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { runBatch } from "@/lib/batch-queue";
import {
  ITEM_TIMEOUT_MS,
  TITLE_MAX,
  addButtonLabel,
  addableCount,
  nonPdfNotice,
  overCapNotice,
  splitPdfs,
  titleFromFilename,
  uploadFailure,
  withTimeout,
  type BatchStatus,
} from "@/lib/paper-batch";
import { createPaper, deletePaper, ingestPaper, suggestTitle } from "@/lib/projects";

type BatchItem = {
  id: string;
  file: File;
  name: string;
  title: string;
  abstract: string | null;
  body: string | null;
  status: BatchStatus;
  error?: string;
};

/**
 * Upload: a batch of PDFs, each read for a suggested title on arrival, then
 * created and ingested on Add.
 *
 * The prototype's per-file progress bar is shown while a file saves, but in
 * an INDETERMINATE (pulsing) state: the upload is a single `fetch`, which
 * reports no upload progress, so any percentage drawn here would be made up.
 */
export function AddPaperUploadTab({
  projectId,
  initialFiles,
  onSaved,
  onDone,
  onBusyChange,
}: {
  projectId: string;
  /**
   * Files the caller already collected (a drop on the library rail).
   * Consumed on ARRAY IDENTITY, so the caller hands over a fresh array per
   * drop; a reused one reads as "the same files again" and is ignored.
   */
  initialFiles?: File[];
  onSaved: () => void;
  onDone: () => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [items, setItemsState] = useState<BatchItem[]>([]);
  const [nonPdf, setNonPdf] = useState(0);
  const [overCap, setOverCap] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  // Ref mirror of `items`. Async workers read the CURRENT list synchronously
  // (a title may be edited while other rows are still extracting), and React
  // batches state updates, so reading through setState is unreliable.
  const itemsRef = useRef<BatchItem[]>([]);
  const setItems = (next: BatchItem[] | ((prev: BatchItem[]) => BatchItem[])) => {
    const value = typeof next === "function" ? next(itemsRef.current) : next;
    itemsRef.current = value;
    setItemsState(value);
  };
  const update = (id: string, patch: Partial<BatchItem>) =>
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));

  // Runs on mount too, which is when the dialog opens carrying a drop.
  useEffect(() => {
    if (initialFiles && initialFiles.length) void addFiles(initialFiles);
    // `addFiles` is redeclared every render; `initialFiles` alone is the
    // intended trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialFiles]);

  async function addFiles(files: File[]) {
    const split = splitPdfs(files, itemsRef.current.length);
    if (split.nonPdf > 0) setNonPdf((n) => n + split.nonPdf);
    if (split.overCap > 0) setOverCap((n) => n + split.overCap);
    if (split.accepted.length === 0) return;

    const fresh: BatchItem[] = split.accepted.map((file) => ({
      id: crypto.randomUUID(),
      file,
      name: file.name,
      title: "",
      abstract: null,
      body: null,
      status: "pending",
    }));
    setItems((prev) => [...prev, ...fresh]);

    await runBatch(fresh, async (item) => {
      update(item.id, { status: "extracting" });
      try {
        const bytes = await item.file.arrayBuffer();
        const meta = await withTimeout(suggestTitle(projectId, bytes), ITEM_TIMEOUT_MS, "suggestTitle");
        update(item.id, {
          status: "ready",
          title: meta.title ? meta.title.slice(0, TITLE_MAX) : titleFromFilename(item.name),
          abstract: meta.abstract,
          body: meta.body,
        });
      } catch {
        // Fail open: the filename remains a usable title.
        update(item.id, { status: "ready", title: titleFromFilename(item.name) });
      }
    });
  }

  async function submit() {
    // Read from the ref, not render state — titles may have been edited
    // moments ago.
    const queue = itemsRef.current.filter((it) => it.status === "ready");
    if (queue.length === 0 || submitting) return;
    setSubmitting(true);

    await runBatch(queue, async (item) => {
      update(item.id, { status: "saving", error: undefined });
      let paperId: string | null = null;
      try {
        const live = itemsRef.current.find((c) => c.id === item.id) ?? item;
        const paper = await withTimeout(
          createPaper(projectId, {
            title: live.title.trim() || titleFromFilename(live.name),
            abstract: live.abstract,
            body: live.body,
            source: "upload",
          }),
          ITEM_TIMEOUT_MS,
          "createPaper"
        );
        paperId = paper.id;
        const bytes = await item.file.arrayBuffer();
        await withTimeout(ingestPaper(projectId, paper.id, bytes), ITEM_TIMEOUT_MS, "ingestPaper");
        update(item.id, { status: "done" });
      } catch {
        // Never leave a paper row without its PDF.
        let cleanedUp = true;
        if (paperId) {
          try {
            await deletePaper(projectId, paperId);
          } catch (cleanupErr) {
            cleanedUp = false;
            console.error("paper cleanup failed after ingest error", { paperId, cleanupErr });
          }
        }
        update(item.id, { status: "failed", error: uploadFailure(cleanedUp) });
      }
    });

    setSubmitting(false);
    onSaved();
    if (itemsRef.current.every((it) => it.status === "done")) onDone();
  }

  const extracting = items.some((it) => it.status === "pending" || it.status === "extracting");
  const count = addableCount(items);

  useEffect(() => {
    onBusyChange(extracting || submitting);
  }, [extracting, submitting, onBusyChange]);

  return (
    <div className="space-y-3">
      <label
        className="flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-6 text-center hover:bg-muted/40"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          void addFiles(Array.from(e.dataTransfer.files));
        }}
      >
        <span className="text-[13px] font-medium">Drop PDFs here or click to choose files</span>
        <span className="text-[12px] text-muted-foreground">A batch is capped at 20 files.</span>
        <input
          type="file"
          multiple
          accept=".pdf"
          className="sr-only"
          onChange={(e) => {
            if (e.target.files) void addFiles(Array.from(e.target.files));
            e.target.value = "";
          }}
        />
      </label>

      {nonPdf > 0 && <p className="text-[12px] text-muted-foreground">{nonPdfNotice(nonPdf)}</p>}
      {overCap > 0 && <p className="text-[12px] text-muted-foreground">{overCapNotice(overCap)}</p>}

      {items.length > 0 && (
        <ul className="space-y-2">
          {items.map((it) => (
            <li key={it.id} className="rounded-md border p-2.5">
              <div className="flex items-center justify-between gap-2">
                <p className="min-w-0 truncate text-[13px] font-medium">{it.name}</p>
                <span className="shrink-0 text-[11px] uppercase tracking-wide text-muted-foreground">
                  {it.status === "extracting" && (
                    <span className="inline-flex items-center gap-1">
                      <Loader2 className="size-3 animate-spin" aria-hidden /> extracting
                    </span>
                  )}
                  {it.status !== "extracting" && it.status}
                </span>
              </div>
              {(it.status === "ready" || it.status === "saving" || it.status === "done") && (
                <Input
                  className="mt-2 h-8 text-[13px]"
                  value={it.title}
                  maxLength={TITLE_MAX}
                  disabled={it.status !== "ready"}
                  onChange={(e) => update(it.id, { title: e.target.value })}
                  aria-label={`Title for ${it.name}`}
                />
              )}
              {/* Indeterminate while saving — see the component note. */}
              {it.status === "saving" && <Progress className="mt-2 animate-pulse" value={null} />}
              {it.status === "done" && <Progress className="mt-2" value={100} />}
              {it.status === "failed" && (
                <div className="mt-1.5 space-y-1.5">
                  <p className="text-[12px] text-destructive">{it.error}</p>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={submitting}
                      onClick={() => update(it.id, { status: "ready", error: undefined })}
                    >
                      Retry
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={submitting}
                      onClick={() => setItems((prev) => prev.filter((x) => x.id !== it.id))}
                    >
                      Remove
                    </Button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center justify-end pt-1">
        <Button disabled={count === 0 || submitting || extracting} onClick={() => void submit()}>
          {addButtonLabel(count, submitting)}
        </Button>
      </div>
    </div>
  );
}
