"use client";

import { useEffect, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  MAX_BATCH,
  PaperRow,
  TITLE_MAX,
  type BatchItem,
} from "@/components/paper-row-fields";
import { runBatch } from "@/lib/batch-queue";
import { createPaper, deletePaper, ingestPaper, suggestTitle } from "@/lib/projects";

// A hung request must never wedge the batch: without a bound, runBatch never
// resolves, `saving` never clears, and the busy-guard leaves the dialog
// permanently undismissable. Generous enough for a large PDF upload + ingest.
const ITEM_TIMEOUT_MS = 120_000;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer)) as Promise<T>;
}

export function PaperUploadScreen({
  projectId,
  onSaved,
  onClose,
  onBusyChange,
}: {
  projectId: string;
  onSaved: () => void;
  onClose: () => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [items, setItemsState] = useState<BatchItem[]>([]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Ref mirror of `items`. Async workers need to read the *current* list
  // synchronously (the user edits titles while extraction is still running),
  // and React batches state updates, so reading through setState is unreliable.
  const itemsRef = useRef<BatchItem[]>([]);
  const setItems = (next: BatchItem[] | ((prev: BatchItem[]) => BatchItem[])) => {
    const value = typeof next === "function" ? next(itemsRef.current) : next;
    itemsRef.current = value;
    setItemsState(value);
  };

  const update = (id: string, patch: Partial<BatchItem>) =>
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));

  async function addFiles(files: File[]) {
    const pdfs = files.filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    const nonPdf = files.length - pdfs.length;
    const room = MAX_BATCH - itemsRef.current.length;
    const accepted = pdfs.slice(0, Math.max(room, 0));
    const overCap = pdfs.length - accepted.length;

    const notes: string[] = [];
    if (nonPdf > 0) notes.push(`${nonPdf} non-PDF file${nonPdf === 1 ? "" : "s"} skipped.`);
    if (overCap > 0) notes.push(`Only ${MAX_BATCH} files at a time — ${overCap} skipped.`);
    if (notes.length) setNotice(notes.join(" "));

    if (!accepted.length) return;

    const fresh: BatchItem[] = accepted.map((f) => ({
      id: crypto.randomUUID(),
      source: f,
      label: f.name,
      title: f.name.replace(/\.pdf$/i, "").slice(0, TITLE_MAX),
      abstract: null,
      body: null,
      status: "pending",
    }));
    setItems((prev) => [...prev, ...fresh]);

    await runBatch(fresh, async (item) => {
      update(item.id, { status: "extracting" });
      try {
        const bytes = await (item.source as File).arrayBuffer();
        const meta = await withTimeout(suggestTitle(projectId, bytes), ITEM_TIMEOUT_MS, "suggestTitle");
        update(item.id, {
          status: "ready",
          title: meta.title ? meta.title.slice(0, TITLE_MAX) : item.title,
          abstract: meta.abstract,
          body: meta.body,
        });
      } catch {
        // Fail open: the filename remains a usable title.
        update(item.id, { status: "ready" });
      }
    });
  }

  async function handleSave() {
    // Read from the ref, not render state — titles may have been edited moments ago.
    const queue = itemsRef.current.filter(
      (it) => it.status === "ready" || it.status === "failed"
    );
    if (!queue.length || saving) return;
    setSaving(true);
    setNotice(null);

    await runBatch(queue, async (item) => {
      update(item.id, { status: "saving", error: undefined });
      let paperId: string | null = null;
      try {
        // Re-read the title: the user may have edited it while this row queued.
        const live = itemsRef.current.find((c) => c.id === item.id) ?? item;
        const paper = await withTimeout(
          createPaper(projectId, {
            title: live.title.trim() || live.label,
            abstract: live.abstract,
            body: live.body,
            source: "upload",
          }),
          ITEM_TIMEOUT_MS,
          "createPaper"
        );
        paperId = paper.id;
        const bytes = await (item.source as File).arrayBuffer();
        await withTimeout(ingestPaper(projectId, paper.id, bytes), ITEM_TIMEOUT_MS, "ingestPaper");
        update(item.id, { status: "done" });
      } catch {
        // Never leave a paper row without its PDF. If the compensating delete
        // also fails, surface that distinctly — a generic "failed" here would
        // let a retry call createPaper again and compound the orphan.
        let cleanedUp = true;
        if (paperId) {
          try {
            await deletePaper(projectId, paperId);
          } catch (cleanupErr) {
            cleanedUp = false;
            console.error("paper cleanup failed after ingest error", { paperId, cleanupErr });
          }
        }
        update(item.id, {
          status: "failed",
          error: cleanedUp
            ? "Couldn't read or index this PDF."
            : "Couldn't index this PDF, and cleanup failed — check the paper list for a leftover entry.",
        });
      }
    });

    setSaving(false);
    onSaved();

    const allDone = itemsRef.current.every((it) => it.status === "done");
    setItems((prev) => prev.filter((it) => it.status !== "done"));
    if (allDone) onClose();
    else setNotice("Some files couldn't be added. Fix or remove them, then try again.");
  }

  const readyCount = items.filter((it) => it.status === "ready" || it.status === "failed").length;
  const busy = items.some((it) => it.status === "extracting" || it.status === "saving");

  // Report extraction/save activity up so the parent dialog can lock the
  // method selector and its own close path — this screen can be doing async
  // work (in-flight uploads/ingest) that the parent has no other visibility into.
  useEffect(() => {
    onBusyChange?.(busy || saving);
  }, [busy, saving, onBusyChange]);

  return (
    <div className="flex flex-col gap-3">
      <input
        ref={fileRef}
        type="file"
        accept=".pdf"
        multiple
        hidden
        onChange={(e) => {
          void addFiles(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />

      <div
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          void addFiles(Array.from(e.dataTransfer.files));
        }}
        className="cursor-pointer rounded-lg border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground transition-colors hover:border-foreground/30"
      >
        <Upload className="mx-auto mb-1.5 size-4" />
        Drop PDFs here, or click to browse — up to {MAX_BATCH} at a time
      </div>

      {items.map((item) => (
        <PaperRow
          key={item.id}
          item={item}
          onTitleChange={(title) => update(item.id, { title })}
          onRemove={() => setItems((prev) => prev.filter((it) => it.id !== item.id))}
        />
      ))}

      {notice && <p className="text-xs text-muted-foreground">{notice}</p>}

      {items.length > 0 && (
        <div className="flex gap-2">
          <Button className="flex-1" onClick={handleSave} disabled={!readyCount || saving || busy}>
            {saving ? "Adding…" : `Add ${readyCount} Paper${readyCount === 1 ? "" : "s"}`}
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}
