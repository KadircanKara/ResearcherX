"use client";

import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ITEM_TIMEOUT_MS,
  MAX_BATCH,
  PaperRow,
  TITLE_MAX,
  withTimeout,
  type BatchItem,
} from "@/components/paper-row-fields";
import { runBatch } from "@/lib/batch-queue";
import {
  createPaper,
  deletePaper,
  ingestPaperFromUrl,
  suggestTitleFromUrl,
} from "@/lib/projects";

const newRow = (): BatchItem => ({
  id: crypto.randomUUID(),
  source: "",
  label: "",
  title: "",
  abstract: null,
  body: null,
  status: "pending",
});

export function PaperLinkScreen({
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
  const [items, setItemsState] = useState<BatchItem[]>(() => [newRow()]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Same ref-mirror rationale as PaperUploadScreen: async workers must read the
  // current list synchronously, and React batches state updates.
  const itemsRef = useRef<BatchItem[]>(items);
  const setItems = (next: BatchItem[] | ((prev: BatchItem[]) => BatchItem[])) => {
    const value = typeof next === "function" ? next(itemsRef.current) : next;
    itemsRef.current = value;
    setItemsState(value);
  };

  const update = (id: string, patch: Partial<BatchItem>) =>
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));

  async function extract(item: BatchItem) {
    const url = String(item.source).trim();
    if (!url || item.status === "extracting") return;
    update(item.id, { status: "extracting", label: url, error: undefined });
    try {
      const meta = await withTimeout(
        suggestTitleFromUrl(projectId, url),
        ITEM_TIMEOUT_MS,
        "suggestTitleFromUrl"
      );
      update(item.id, {
        status: "ready",
        title: meta.title ? meta.title.slice(0, TITLE_MAX) : url,
        abstract: meta.abstract,
      });
    } catch {
      update(item.id, { status: "ready", title: item.title || url });
    }
  }

  async function handleSave() {
    // Read from the ref, not render state — a row may have been edited or
    // extracted moments ago and React may not have flushed that into `items`.
    const queue = itemsRef.current.filter((it) => String(it.source).trim());
    if (!queue.length || saving) return;
    setSaving(true);
    setNotice(null);

    await runBatch(queue, async (item) => {
      const url = String(item.source).trim();
      // A row the user never blurred still needs its metadata.
      if (item.status === "pending") await extract(item);

      update(item.id, { status: "saving", error: undefined });
      let paperId: string | null = null;
      try {
        // Re-read after extract(): it wrote the title and abstract onto this
        // row. The captured `item` closure variable is stale.
        const live = itemsRef.current.find((c) => c.id === item.id) ?? item;
        const paper = await withTimeout(
          createPaper(projectId, {
            title: live.title.trim() || url,
            abstract: live.abstract,
            body: null,
            pdf_url: url,
            source: "link",
          }),
          ITEM_TIMEOUT_MS,
          "createPaper"
        );
        paperId = paper.id;
        await withTimeout(
          ingestPaperFromUrl(projectId, paper.id, url),
          ITEM_TIMEOUT_MS,
          "ingestPaperFromUrl"
        );
        update(item.id, { status: "done" });
      } catch (e) {
        // Paywalled URLs are a normal outcome here, not a crash — URL rows
        // fail far more often than local files, so surface the specific
        // reason instead of sending the user hunting for a bug that isn't
        // there. Cleanup failure is surfaced distinctly too — PATCH 422s
        // abstract/body on a link paper and there's no re-ingest affordance,
        // so a leftover scraped-but-unindexed row can only be fixed by
        // deleting it, and the user needs to know to go find it.
        let cleanedUp = true;
        if (paperId) {
          try {
            await deletePaper(projectId, paperId);
          } catch (cleanupErr) {
            cleanedUp = false;
            console.error("paper cleanup failed after ingest error", { paperId, cleanupErr });
          }
        }
        const paywalled =
          e instanceof Error && (e as Error & { paywalled?: boolean }).paywalled;
        // Indexing being down is not the link's fault — saying "couldn't fetch"
        // sends the user to re-check a URL that worked fine.
        const unavailable =
          e instanceof Error && (e as Error & { unavailable?: boolean }).unavailable;
        update(item.id, {
          status: "failed",
          error: !cleanedUp
            ? "Couldn't fetch this paper, and cleanup failed — check the paper list for a leftover entry."
            : paywalled
              ? "Paywalled — upload the PDF instead."
              : unavailable
                ? "Indexing is temporarily unavailable. Try again later."
                : "Couldn't fetch this paper.",
        });
      }
    });

    setSaving(false);
    onSaved();

    const allDone = itemsRef.current.every((it) => it.status === "done");
    setItems((prev) => prev.filter((it) => it.status !== "done"));
    if (allDone) onClose();
    else setNotice("Some links couldn't be added. Fix or remove them, then try again.");
  }

  const filled = items.filter((it) => String(it.source).trim()).length;
  const busy = items.some((it) => it.status === "extracting" || it.status === "saving");

  // Report extraction/save activity up so the parent dialog can lock the
  // method selector and its own close path — this screen can be doing async
  // work (in-flight fetch/ingest) that the parent has no other visibility into.
  useEffect(() => {
    onBusyChange?.(busy || saving);
  }, [busy, saving, onBusyChange]);

  return (
    <div className="flex flex-col gap-3">
      {items.map((item) =>
        item.status === "pending" ? (
          <div key={item.id} className="flex items-center gap-2">
            <Input
              placeholder="https://arxiv.org/abs/…"
              value={String(item.source)}
              onChange={(e) => update(item.id, { source: e.target.value })}
              onBlur={() => void extract(item)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void extract(item);
              }}
              className="flex-1"
            />
            {items.length > 1 && (
              <Button
                variant="ghost"
                onClick={() => setItems((prev) => prev.filter((it) => it.id !== item.id))}
              >
                Remove
              </Button>
            )}
          </div>
        ) : (
          <PaperRow
            key={item.id}
            item={item}
            onTitleChange={(title) => update(item.id, { title })}
            onRemove={() => setItems((prev) => prev.filter((it) => it.id !== item.id))}
          />
        )
      )}

      <Button
        variant="outline"
        className="self-start"
        disabled={items.length >= MAX_BATCH || saving}
        onClick={() => setItems((prev) => [...prev, newRow()])}
      >
        <Plus className="mr-1.5 size-3.5" />
        Add URL
      </Button>

      {notice && <p className="text-xs text-muted-foreground">{notice}</p>}

      <div className="flex gap-2">
        <Button className="flex-1" onClick={handleSave} disabled={!filled || saving || busy}>
          {saving ? "Adding…" : `Add ${filled} Paper${filled === 1 ? "" : "s"}`}
        </Button>
        <Button variant="ghost" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
