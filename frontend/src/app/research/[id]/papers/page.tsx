"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { Download, ExternalLink, FileText, Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { SearchInput } from "@/components/ui/search-input";
import { PaperDialog } from "@/components/paper-dialog";
import { getProject, listPapers, deletePaper, fetchPaperPdf } from "@/lib/projects";
import { saveBlob } from "@/lib/download";
import { clear, isAllSelected, retainVisible, selectAll, toggle } from "@/lib/selection";
import { matchesQuery } from "@/lib/search";
import type { Paper, Role } from "@/lib/types";

const CAN_ADD: Role[] = ["owner", "member"];

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function PapersPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [myRole, setMyRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const [editingMode, setEditingMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  /** Title and abstract -- the two things the row actually shows, so every
   * match is visible and nothing reads as a false positive. */
  const searchable = (paper: Paper) => [paper.title, paper.abstract];

  function changeQuery(next: string) {
    setQuery(next);
    // Selections that just left the screen go with it: Delete must never
    // reach a row the user cannot see.
    const stillVisible = papers.filter((p) => matchesQuery(next, searchable(p))).map((p) => p.id);
    setSelected((prev) => retainVisible(prev, stillVisible));
  }

  async function handleDownloadPdf(paper: Paper) {
    setDownloading(paper.id);
    try {
      const blob = await fetchPaperPdf(projectId, paper.id);
      // Named for the paper, not its id: the file lands in a downloads
      // folder where an id names nothing. Same character rules as the chat
      // transcript export, for the same filesystems.
      const safe = paper.title.replace(/[\\/:*?"<>|]/g, "-").trim().slice(0, 80) || "paper";
      saveBlob(blob, `${safe}.pdf`);
    } catch {
      setBulkError("Could not download that PDF. Please try again.");
    } finally {
      setDownloading(null);
    }
  }
  const [editing, setEditing] = useState<Paper | null>(null);

  // `silent` skips the full-page loading skeleton. The skeleton branch below
  // doesn't render <PaperDialog>, so a non-silent reload while the Add Paper
  // dialog is open unmounts it out from under the user — e.g. PaperUploadScreen
  // calls onSaved (this function) mid-batch, and an open dialog would vanish
  // instead of staying open to show a failed row.
  //
  // `loadSeq` guards against out-of-order resolution: onSaved (batch
  // completion) and handleDelete's error-path resync can both be in flight at
  // once, and a slower earlier request resolving after a faster later one
  // would otherwise overwrite fresher state with stale data.
  const loadSeq = useRef(0);

  const load = useCallback((opts: { silent?: boolean } = {}) => {
    const seq = ++loadSeq.current;
    if (!opts.silent) setLoading(true);
    Promise.all([listPapers(projectId), getProject(projectId)])
      .then(([ps, detail]) => {
        if (seq !== loadSeq.current) return; // a newer load already won
        setPapers(ps);
        setMyRole(detail.my_role);
      })
      .catch(() => {})
      .finally(() => {
        if (seq === loadSeq.current) setLoading(false);
      });
  }, [projectId]);

  async function handleDelete(paperId: string) {
    setDeleting(paperId);
    try {
      await deletePaper(projectId, paperId);
      setPapers((prev) => prev.filter((p) => p.id !== paperId));
    } catch {
      load({ silent: true });
    } finally {
      setDeleting(null);
    }
  }

  // Asked through a real dialog, never `window.confirm` -- see
  // `ConfirmDialog`: a page that fires several native dialogs gets them
  // SUPPRESSED by Chrome, after which `confirm()` returns false without
  // opening anything and the delete silently does nothing.
  const [pendingBulkDelete, setPendingBulkDelete] = useState(false);

  async function handleBulkDelete() {
    setPendingBulkDelete(false);
    setBulkBusy(true);
    setBulkError(null);
    const ids = [...selected];
    const results = await Promise.allSettled(
      ids.map((id) => deletePaper(projectId, id))
    );
    const failed = ids.filter((_, i) => results[i].status === "rejected");
    setSelected(new Set(failed));
    if (failed.length > 0) {
      setBulkError(`${failed.length} of ${ids.length} could not be deleted.`);
    }
    setBulkBusy(false);
    // Re-fetched unconditionally: what just proved unreliable is precisely
    // this client's idea of what exists.
    load({ silent: true });
  }

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  const canAdd = myRole !== null && CAN_ADD.includes(myRole);
  const visible = papers.filter((p) => matchesQuery(query, searchable(p)));
  const visibleIds = visible.map((p) => p.id);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {papers.length === 0
            ? "No papers yet"
            : query
              ? `${visible.length} of ${papers.length} papers`
              : `${papers.length} paper${papers.length !== 1 ? "s" : ""}`}
        </p>
        <div className="flex items-center gap-2">
          {papers.length > 0 && (
            <div className="w-56">
              <SearchInput
                value={query}
                onChange={changeQuery}
                placeholder="Search papers…"
                label="Search papers by title or abstract"
              />
            </div>
          )}
          <BulkEditBar
            active={editingMode}
            count={selected.size}
            total={visibleIds.length}
            allSelected={isAllSelected(selected, visibleIds)}
            busy={bulkBusy}
            onEnter={() => setEditingMode(true)}
            onSelectAll={() => setSelected(selectAll(selected, visibleIds))}
            onClear={() => setSelected(clear())}
            onDelete={() => setPendingBulkDelete(true)}
            onDone={() => {
              setEditingMode(false);
              // A selection that survives invisibly is a delete waiting to
              // hit the wrong rows.
              setSelected(clear());
            }}
          />
          {canAdd && (
            <PaperDialog projectId={projectId} onSaved={() => load({ silent: true })}>
              <Button size="sm">
                <Plus className="mr-1.5 size-3.5" />
                Add Paper
              </Button>
            </PaperDialog>
          )}
        </div>
      </div>

      {bulkError && (
        <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {bulkError}
        </p>
      )}

      {papers.length === 0 && (
        <div className="flex flex-col items-center gap-3 py-24 text-center">
          <FileText className="size-8 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">
            {canAdd
              ? "Add papers to enable RAG chat on this project."
              : "No papers have been added yet."}
          </p>
        </div>
      )}

      {/* A query that matches nothing needs saying: an empty list under a
          filled search box otherwise reads as the library having emptied. */}
      {papers.length > 0 && visible.length === 0 && (
        <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          No papers match “{query}”.
        </p>
      )}

      <div className="space-y-2">
        {visible.map((paper) => (
          <div
            key={paper.id}
            className="flex items-start gap-3 rounded-xl border border-border bg-card px-4 py-3"
          >
            {editingMode && (
              <input
                type="checkbox"
                checked={selected.has(paper.id)}
                onChange={() => setSelected(toggle(selected, paper.id))}
                aria-label={`Select ${paper.title}`}
                className="mt-1 size-4 shrink-0"
              />
            )}
            <div className="min-w-0 flex-1">
              <p className="line-clamp-1 text-sm font-medium text-foreground">
                {paper.title}
              </p>
              {paper.abstract && (
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {paper.abstract}
                </p>
              )}
              <p className="mt-1.5 text-xs text-muted-foreground/60">
                {fmtDate(paper.created_at)}
              </p>
            </div>
            {/* Three states, one slot. A paper we hold the PDF for is
                downloadable; a link-sourced one opens where it lives; and a
                paper with neither -- every row ingested before PDFs were
                kept -- says so rather than leaving an unexplained gap where
                its neighbours have a control. */}
            {paper.has_pdf ? (
              <button
                onClick={() => void handleDownloadPdf(paper)}
                disabled={downloading === paper.id}
                title="Download PDF"
                aria-label={`Download PDF: ${paper.title}`}
                className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
              >
                <Download className="size-3.5" />
              </button>
            ) : paper.resolved_pdf_url || paper.pdf_url ? (
              <a
                href={paper.resolved_pdf_url ?? paper.pdf_url ?? undefined}
                target="_blank"
                rel="noopener noreferrer"
                title="Open the paper's link"
                aria-label={`Open link: ${paper.title}`}
                className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-accent hover:text-foreground"
              >
                <ExternalLink className="size-3.5" />
              </a>
            ) : (
              <span
                title="No PDF stored — this paper was added before PDFs were kept."
                className="mt-0.5 shrink-0 cursor-default rounded p-1 text-muted-foreground/25"
                aria-label="No PDF stored"
              >
                <Download className="size-3.5" />
              </span>
            )}
            {canAdd && (
              <button
                onClick={() => setEditing(paper)}
                className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-accent hover:text-foreground"
                aria-label="Edit paper"
              >
                <Pencil className="size-3.5" />
              </button>
            )}
            {canAdd && (
              <button
                onClick={() => handleDelete(paper.id)}
                disabled={deleting === paper.id}
                className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                aria-label="Delete paper"
              >
                <Trash2 className="size-3.5" />
              </button>
            )}
          </div>
        ))}
      </div>

      {editing && (
        <PaperDialog
          projectId={projectId}
          paper={editing}
          open={!!editing}
          onOpenChange={(o) => !o && setEditing(null)}
          onSaved={() => {
            setEditing(null);
            // Silent: a non-silent load flips `loading` true, and that branch
            // replaces the whole page with skeletons — flashing the entire list
            // away for a single-field edit and losing scroll position.
            load({ silent: true });
          }}
        />
      )}

      <ConfirmDialog
        open={pendingBulkDelete}
        title={`Delete ${selected.size} paper${selected.size !== 1 ? "s" : ""}?`}
        description="This cannot be undone."
        confirmLabel="Delete"
        busy={bulkBusy}
        onCancel={() => setPendingBulkDelete(false)}
        onConfirm={() => void handleBulkDelete()}
      />
    </div>
  );
}
