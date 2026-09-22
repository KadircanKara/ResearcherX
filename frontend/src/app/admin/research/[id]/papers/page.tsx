"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { Plus } from "lucide-react";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { PageHeader } from "@/components/page-header";
import { LibraryRail } from "@/components/papers/library-rail";
import { PaperDialog } from "@/components/papers/paper-dialog";
import { PaperTable } from "@/components/papers/paper-table";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState, NoMatchState } from "@/components/ui/empty-state";
import { SearchInput } from "@/components/ui/search-input";
import { saveBlob } from "@/lib/download";
import {
  deletePaper,
  fetchPaperPdf,
  getProject,
  listPapers,
  probePaperIndexed,
} from "@/lib/projects";
import { lastAddedLabel, libraryHeadline, summarize, type ProbeMap } from "@/lib/papers";
import { matchesQuery } from "@/lib/search";
import { clear, isAllSelected, retainVisible, selectAll, toggle } from "@/lib/selection";
import type { Paper, Role } from "@/lib/types";

const CAN_ADD: Role[] = ["owner", "member"];

/** Title and abstract -- the two things a row shows once opened, so every
 * match is visible and nothing reads as a false positive. */
const searchable = (paper: Paper) => [paper.title, paper.abstract];

export default function PapersPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [myRole, setMyRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [editing, setEditing] = useState<Paper | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const [editingMode, setEditingMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  // Asked through a real dialog, never `window.confirm` -- see
  // `ConfirmDialog`: Chrome suppresses repeated native dialogs, after which
  // `confirm()` returns false without opening and the delete silently fails.
  const [pendingBulkDelete, setPendingBulkDelete] = useState(false);
  const [pendingRemove, setPendingRemove] = useState<Paper | null>(null);

  // One row open at a time, like the concept: the opened body is wide and two
  // of them stacked push the rest of the table off screen.
  const [openId, setOpenId] = useState<string | null>(null);
  // What the retriever answered about each paper, keyed by id. Never fetched
  // in a sweep -- one request, when a row is opened. See `lib/papers.ts`.
  const [probes, setProbes] = useState<ProbeMap>({});

  const [addOpen, setAddOpen] = useState(false);
  // A fresh array per drop, deliberately: `PaperUploadScreen` consumes it on
  // identity change, so reusing one would swallow the second drop.
  const [droppedFiles, setDroppedFiles] = useState<File[]>([]);

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

  const load = useCallback(
    (opts: { silent?: boolean } = {}) => {
      const seq = ++loadSeq.current;
      if (!opts.silent) setLoading(true);
      Promise.all([listPapers(projectId), getProject(projectId)])
        .then(([ps, detail]) => {
          if (seq !== loadSeq.current) return; // a newer load already won
          setPapers(ps);
          setMyRole(detail.my_role);
          // A probe is a claim about a paper that still exists. Dropping the
          // rest keeps a deleted paper's answer from being reused by a new
          // paper that happens to reuse nothing but the shape of the map.
          setProbes((prev) => {
            const live: ProbeMap = {};
            for (const p of ps) if (prev[p.id]) live[p.id] = prev[p.id];
            return live;
          });
        })
        .catch(() => {})
        .finally(() => {
          if (seq === loadSeq.current) setLoading(false);
        });
    },
    [projectId]
  );

  const probe = useCallback(
    (paperId: string) => {
      setProbes((prev) => ({ ...prev, [paperId]: "checking" }));
      void probePaperIndexed(projectId, paperId).then((result) => {
        setProbes((prev) =>
          // Only write back if this paper is still being tracked: a delete
          // between the request and its answer already pruned the map, and
          // re-adding the key would resurrect a row's state in `summarize`.
          prev[paperId] === undefined ? prev : { ...prev, [paperId]: result }
        );
      });
    },
    [projectId]
  );

  function toggleRow(paper: Paper) {
    const next = openId === paper.id ? null : paper.id;
    setOpenId(next);
    // Asked on open, and again only if the previous attempt failed outright.
    const seen = probes[paper.id];
    if (next && (seen === undefined || seen === "unavailable")) probe(paper.id);
  }

  async function handleDelete(paperId: string) {
    setPendingRemove(null);
    setDeleting(paperId);
    try {
      await deletePaper(projectId, paperId);
      setPapers((prev) => prev.filter((p) => p.id !== paperId));
      setProbes((prev) => {
        const next = { ...prev };
        delete next[paperId];
        return next;
      });
      if (openId === paperId) setOpenId(null);
    } catch {
      load({ silent: true });
    } finally {
      setDeleting(null);
    }
  }

  function changeQuery(next: string) {
    setQuery(next);
    // Selections that just left the screen go with it: Delete must never
    // reach a row the user cannot see.
    const stillVisible = papers
      .filter((p) => matchesQuery(next, searchable(p)))
      .map((p) => p.id);
    setSelected((prev) => retainVisible(prev, stillVisible));
  }

  async function handleDownloadPdf(paper: Paper) {
    setDownloading(paper.id);
    try {
      const blob = await fetchPaperPdf(projectId, paper.id);
      // Named for the paper, not its id: an id names nothing in a downloads
      // folder. Same character rules as the chat transcript export.
      const safe =
        paper.title
          .replace(/[\\/:*?"<>|]/g, "-")
          .trim()
          .slice(0, 80) || "paper";
      saveBlob(blob, `${safe}.pdf`);
    } catch {
      setBulkError("Could not download that PDF. Please try again.");
    } finally {
      setDownloading(null);
    }
  }

  async function handleBulkDelete() {
    setPendingBulkDelete(false);
    setBulkBusy(true);
    setBulkError(null);
    const ids = [...selected];
    const results = await Promise.allSettled(ids.map((id) => deletePaper(projectId, id)));
    const failed = ids.filter((_, i) => results[i].status === "rejected");
    setSelected(new Set(failed));
    if (failed.length > 0) {
      setBulkError(`${failed.length} of ${ids.length} could not be deleted.`);
    }
    if (openId !== null && ids.includes(openId) && !failed.includes(openId)) {
      setOpenId(null);
    }
    setBulkBusy(false);
    // Re-fetched unconditionally: what just proved unreliable is precisely
    // this client's idea of what exists.
    load({ silent: true });
  }

  function openDialogWith(files: File[]) {
    setDroppedFiles(files);
    setAddOpen(true);
  }

  useEffect(() => {
    load();
  }, [load]);

  const canAdd = myRole !== null && CAN_ADD.includes(myRole);
  const visible = papers.filter((p) => matchesQuery(query, searchable(p)));
  const visibleIds = visible.map((p) => p.id);
  const summary = summarize(papers, probes);

  return (
    <div className="fade-block space-y-5">
      <PageHeader
        eyebrow="Library"
        title={loading ? "Reading the library" : libraryHeadline(summary)}
        meta={lastAddedLabel(papers)}
        actions={
          canAdd ? (
            <Button size="sm" onClick={() => openDialogWith([])}>
              <Plus className="size-4" aria-hidden />
              Add papers
            </Button>
          ) : undefined
        }
      />

      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="min-w-0 flex-1 space-y-4">
          <p className="max-w-2xl text-[13px] text-muted-foreground">
            Open a paper to see what the retriever holds for it. Only papers the retriever
            holds text for can be searched or mentioned in a question.
          </p>

          {papers.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="w-full sm:w-72">
                <SearchInput
                  value={query}
                  onChange={changeQuery}
                  placeholder="Search papers…"
                  label="Search papers by title or abstract"
                />
              </div>
              {canAdd && (
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
                    // A selection that survives invisibly is a delete
                    // waiting to hit the wrong rows.
                    setSelected(clear());
                  }}
                />
              )}
            </div>
          )}

          {bulkError && (
            <p role="alert" className="text-[13px] text-destructive">
              {bulkError}
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
              body={
                canAdd
                  ? "Add a paper and it is read, split and embedded on arrival — then it can be searched and mentioned in a question."
                  : "No papers have been added to this project yet."
              }
            >
              {canAdd && (
                <Button size="sm" onClick={() => openDialogWith([])}>
                  Add papers
                </Button>
              )}
            </EmptyState>
          ) : visible.length === 0 ? (
            // A query that matches nothing needs saying: an empty table under
            // a filled search box otherwise reads as the library emptying.
            <NoMatchState query={query} noun="papers" />
          ) : (
            <PaperTable
              papers={visible}
              probes={probes}
              editing={editingMode}
              selectedIds={selected}
              openId={openId}
              canEdit={canAdd}
              downloadingId={downloading}
              deletingId={deleting}
              onToggleSelect={(paper) => setSelected(toggle(selected, paper.id))}
              onToggleOpen={toggleRow}
              onCheckAgain={(paper) => probe(paper.id)}
              onRename={setEditing}
              onRemove={setPendingRemove}
              onDownload={(paper) => void handleDownloadPdf(paper)}
            />
          )}
        </div>

        <LibraryRail summary={summary} canAdd={canAdd} onOpenAdd={openDialogWith} />
      </div>

      <PaperDialog
        projectId={projectId}
        open={addOpen}
        onOpenChange={setAddOpen}
        initialFiles={droppedFiles}
        onSaved={() => load({ silent: true })}
      />

      <ConfirmDialog
        open={pendingRemove !== null}
        title="Remove this paper?"
        description={`“${pendingRemove?.title ?? ""}” will be removed from this library. This cannot be undone.`}
        confirmLabel="Remove"
        busy={deleting !== null}
        onCancel={() => setPendingRemove(null)}
        onConfirm={() => pendingRemove && void handleDelete(pendingRemove.id)}
      />

      <ConfirmDialog
        open={pendingBulkDelete}
        title={`Delete ${selected.size} paper${selected.size !== 1 ? "s" : ""}?`}
        description="This cannot be undone."
        confirmLabel="Delete"
        busy={bulkBusy}
        onCancel={() => setPendingBulkDelete(false)}
        onConfirm={() => void handleBulkDelete()}
      />

      {editing && (
        <PaperDialog
          projectId={projectId}
          paper={editing}
          open={!!editing}
          onOpenChange={(o) => !o && setEditing(null)}
          onSaved={() => {
            const editedId = editing.id;
            setEditing(null);
            // A manual paper's text is re-embedded by the PATCH, so anything
            // this screen already knew about the retriever's contents for it
            // is now a claim about the previous text.
            setProbes((prev) => {
              const next = { ...prev };
              delete next[editedId];
              return next;
            });
            // Silent: a non-silent load flips `loading` true, and that branch
            // replaces the whole table with skeletons — flashing the entire
            // list away for a single-field edit and losing scroll position.
            load({ silent: true });
          }}
        />
      )}
    </div>
  );
}
