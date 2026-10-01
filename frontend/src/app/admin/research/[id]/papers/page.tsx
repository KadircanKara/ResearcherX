"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { Plus, Search } from "lucide-react";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { PageHeader } from "@/components/page-header";
import { AddPaperDialog } from "@/components/papers/add-paper-dialog";
import { LibraryRail } from "@/components/papers/library-rail";
import { PaperTable } from "@/components/papers/paper-table";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState, NoMatchState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { RenameDialog } from "@/components/ui/rename-dialog";
import { fetchUsage } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { extensionForType } from "@/lib/paper-file-types";
import { deletePaper, fetchPaperPdf, listPapers, patchPaper } from "@/lib/projects";
import { lastAddedLabel, libraryHeadline, summarize } from "@/lib/papers";
import { matchesQuery } from "@/lib/search";
import { clear, retainVisible, selectAll, toggle } from "@/lib/selection";
import type { Paper } from "@/lib/types";
import { papersLine, type Usage } from "@/lib/usage";

/** Title and abstract -- the two things a row shows once opened, so every
 * match is visible and nothing reads as a false positive. */
const searchable = (paper: Paper) => [paper.title, paper.abstract];

export default function PapersPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  // One row open at a time: the opened body is wide and two of them stacked
  // push the rest of the table off screen.
  const [openId, setOpenId] = useState<string | null>(null);

  const [addOpen, setAddOpen] = useState(false);
  // A fresh array per open, deliberately: the upload tab consumes it on
  // identity change, so reusing one would swallow the second drop.
  const [droppedFiles, setDroppedFiles] = useState<File[]>([]);

  const [renamePaper, setRenamePaper] = useState<Paper | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  // Asked through a real dialog, never `window.confirm` -- see
  // `ConfirmDialog`: Chrome suppresses repeated native dialogs, after which
  // `confirm()` returns false without opening and the delete silently fails.
  const [removePaper, setRemovePaper] = useState<Paper | null>(null);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  // `silent` skips the loading skeleton. The skeleton replaces the table, so
  // a non-silent reload after a single write would flash the whole list away
  // and lose the scroll position.
  //
  // `loadSeq` guards against out-of-order resolution: an add batch finishing
  // and a delete's error-path resync can both be in flight at once, and a
  // slower earlier request resolving after a faster later one would
  // otherwise overwrite fresher state with stale data.
  const loadSeq = useRef(0);

  const load = useCallback(
    (opts: { silent?: boolean } = {}) => {
      const seq = ++loadSeq.current;
      if (!opts.silent) setLoading(true);
      fetchUsage().then(setUsage).catch(() => {});
      listPapers(projectId)
        .then((ps) => {
          if (seq !== loadSeq.current) return; // a newer load already won
          setPapers(ps);
        })
        .catch(() => {})
        .finally(() => {
          if (seq === loadSeq.current) setLoading(false);
        });
    },
    [projectId]
  );

  useEffect(() => {
    load();
  }, [load]);

  const visible = papers.filter((p) => matchesQuery(query, searchable(p)));
  const summary = summarize(papers);

  function changeQuery(next: string) {
    setQuery(next);
    // Selections that just left the screen go with it: Delete must never
    // reach a row the user cannot see.
    const stillVisible = papers
      .filter((p) => matchesQuery(next, searchable(p)))
      .map((p) => p.id);
    setSelectedIds((prev) => retainVisible(prev, stillVisible));
  }

  function toggleOpen(paper: Paper) {
    setOpenId(openId === paper.id ? null : paper.id);
  }

  function openAdd(files: File[]) {
    setDroppedFiles(files);
    setAddOpen(true);
  }

  async function handleRename(value: string) {
    if (!renamePaper) return;
    setRenameBusy(true);
    setRenameError(null);
    try {
      // A title-only PATCH re-embeds nothing; the returned row carries
      // the same chunk_count.
      const updated = await patchPaper(projectId, renamePaper.id, { title: value });
      setPapers((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      setRenamePaper(null);
    } catch {
      setRenameError("Could not rename this paper. Please try again.");
    } finally {
      setRenameBusy(false);
    }
  }

  async function handleRemoveConfirmed() {
    if (!removePaper) return;
    const paperId = removePaper.id;
    setRemovePaper(null);
    setDeletingId(paperId);
    setError(null);
    try {
      await deletePaper(projectId, paperId);
      setPapers((prev) => prev.filter((p) => p.id !== paperId));
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(paperId);
        return next;
      });
      if (openId === paperId) setOpenId(null);
    } catch {
      setError("Could not remove that paper. Please try again.");
      load({ silent: true });
    } finally {
      setDeletingId(null);
    }
  }

  async function handleBulkDelete() {
    setBulkDeleteOpen(false);
    setBulkBusy(true);
    setError(null);
    const ids = [...selectedIds];
    const results = await Promise.allSettled(ids.map((id) => deletePaper(projectId, id)));
    const failed = ids.filter((_, i) => results[i].status === "rejected");
    if (openId !== null && ids.includes(openId) && !failed.includes(openId)) {
      setOpenId(null);
    }
    if (failed.length > 0) {
      // Stay in edit mode with exactly the rows that survived selected, so
      // the retry is one click away.
      setSelectedIds(new Set(failed));
      setError(`${failed.length} of ${ids.length} could not be deleted.`);
    } else {
      setSelectedIds(clear());
      setEditing(false);
    }
    setBulkBusy(false);
    // Re-fetched unconditionally: what just proved unreliable is precisely
    // this client's idea of what exists.
    load({ silent: true });
  }

  async function handleDownload(paper: Paper) {
    setDownloadingId(paper.id);
    setError(null);
    try {
      const blob = await fetchPaperPdf(projectId, paper.id);
      // Named for the paper, not its id: an id names nothing in a downloads
      // folder. Same character rules as the chat transcript export.
      const safe =
        paper.title
          .replace(/[\\/:*?"<>|]/g, "-")
          .trim()
          .slice(0, 80) || "paper";
      // The served type names the extension: an upload may be any of the
      // supported formats, not only a PDF.
      saveBlob(blob, `${safe}${extensionForType(blob.type)}`);
    } catch {
      setError("Could not download that file. Please try again.");
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Library"
        title={loading ? "Reading the library" : libraryHeadline(summary)}
        meta={loading ? undefined : lastAddedLabel(papers)}
        actions={
          <div className="flex items-center gap-3">
            {usage && papersLine(usage) && (
              <span className="text-xs text-muted-foreground">{papersLine(usage)}</span>
            )}
            <Button size="sm" onClick={() => openAdd([])}>
              <Plus className="size-4" aria-hidden />
              Add papers
            </Button>
          </div>
        }
      />

      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="min-w-0 flex-1 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="relative w-full sm:w-72">
              <Search
                className="absolute left-3 top-2.5 size-4 text-muted-foreground"
                aria-hidden
              />
              <Input
                value={query}
                onChange={(e) => changeQuery(e.target.value)}
                placeholder="Search papers…"
                className="pl-9"
                aria-label="Search papers"
              />
            </div>
            {papers.length > 0 && (
              <BulkEditBar
                editing={editing}
                selectedCount={selectedIds.size}
                busy={bulkBusy}
                onStart={() => setEditing(true)}
                onSelectAll={() =>
                  setSelectedIds(
                    selectAll(
                      selectedIds,
                      visible.map((p) => p.id)
                    )
                  )
                }
                onClear={() => setSelectedIds(clear())}
                onDelete={() => setBulkDeleteOpen(true)}
                onDone={() => {
                  setEditing(false);
                  // A selection that survives invisibly is a delete waiting
                  // to hit the wrong rows.
                  setSelectedIds(clear());
                }}
              />
            )}
          </div>

          {error && (
            <p role="alert" className="text-[13px] text-destructive">
              {error}
            </p>
          )}

          {loading ? (
            <div className="space-y-2" aria-hidden="true">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-16 animate-pulse rounded-lg bg-muted" />
              ))}
            </div>
          ) : papers.length === 0 ? (
            <EmptyState
              title="No papers yet"
              body="Add a paper and it is read, split and embedded on arrival — then it can be searched and mentioned in a question."
            >
              <Button size="sm" onClick={() => openAdd([])}>
                Add papers
              </Button>
            </EmptyState>
          ) : visible.length === 0 ? (
            <NoMatchState query={query} noun="papers" />
          ) : (
            <PaperTable
              papers={visible}
              editing={editing}
              selectedIds={selectedIds}
              onToggleSelect={(paper) => setSelectedIds(toggle(selectedIds, paper.id))}
              openId={openId}
              onToggleOpen={toggleOpen}
              downloadingId={downloadingId}
              deletingId={deletingId}
              onRename={(paper) => {
                setRenameError(null);
                setRenamePaper(paper);
              }}
              onRemove={setRemovePaper}
              onDownload={(paper) => void handleDownload(paper)}
            />
          )}
        </div>

        <LibraryRail summary={summary} onOpenAdd={openAdd} />
      </div>

      <AddPaperDialog
        projectId={projectId}
        open={addOpen}
        onOpenChange={setAddOpen}
        initialFiles={droppedFiles}
        onSaved={() => load({ silent: true })}
      />

      <RenameDialog
        open={renamePaper !== null}
        title="Rename paper"
        label="Title"
        initialValue={renamePaper?.title ?? ""}
        busy={renameBusy}
        error={renameError}
        onCancel={() => setRenamePaper(null)}
        onSubmit={(value) => void handleRename(value)}
      />

      <ConfirmDialog
        open={removePaper !== null}
        title="Remove this paper?"
        description={`"${removePaper?.title ?? ""}" will be removed from this library. This can't be undone.`}
        confirmLabel="Remove"
        onCancel={() => setRemovePaper(null)}
        onConfirm={() => void handleRemoveConfirmed()}
      />

      <ConfirmDialog
        open={bulkDeleteOpen}
        title={`Delete ${selectedIds.size} ${selectedIds.size === 1 ? "paper" : "papers"}?`}
        description="This cannot be undone."
        confirmLabel="Delete"
        onCancel={() => setBulkDeleteOpen(false)}
        onConfirm={() => void handleBulkDelete()}
      />
    </div>
  );
}
