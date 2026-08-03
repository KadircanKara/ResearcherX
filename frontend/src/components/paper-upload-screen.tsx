"use client";

import { useRef, useState } from "react";
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

export function PaperUploadScreen({
  projectId,
  onSaved,
  onClose,
}: {
  projectId: string;
  onSaved: () => void;
  onClose: () => void;
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
    const room = MAX_BATCH - items.length;
    const accepted = pdfs.slice(0, Math.max(room, 0));
    if (pdfs.length > accepted.length) {
      setNotice(`Only ${MAX_BATCH} files at a time — ${pdfs.length - accepted.length} skipped.`);
    }
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
        const meta = await suggestTitle(projectId, bytes);
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
        const paper = await createPaper(projectId, {
          title: live.title.trim() || live.label,
          abstract: live.abstract,
          body: live.body,
          source: "upload",
        });
        paperId = paper.id;
        const bytes = await (item.source as File).arrayBuffer();
        await ingestPaper(projectId, paper.id, bytes);
        update(item.id, { status: "done" });
      } catch {
        // Never leave a paper row without its PDF.
        if (paperId) await deletePaper(projectId, paperId).catch(() => {});
        update(item.id, { status: "failed", error: "Couldn't read or index this PDF." });
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
