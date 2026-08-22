"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Download, FileCode2, Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { RenameDialog } from "@/components/ui/rename-dialog";
import { SearchInput } from "@/components/ui/search-input";
import { ConflictDialog } from "@/components/latex/conflict-dialog";
import { ImportDropzone } from "@/components/latex/import-dropzone";
import { NewDocumentDialog } from "@/components/latex/new-document-dialog";
import {
  createDocument,
  deleteDocument,
  downloadExport,
  listDocuments,
  patchDocument,
  errorText,
  NameCollisionError,
  type LatexCollision,
  type LatexDocument,
} from "@/lib/latex";
import { getProject } from "@/lib/projects";
import {
  clear,
  isAllSelected,
  retainVisible,
  selectAll,
  toggle,
} from "@/lib/selection";
import { matchesQuery } from "@/lib/search";
import { STARTER } from "@/lib/latex-starter";
import type { Role } from "@/lib/types";

const CAN_EDIT: Role[] = ["owner", "member"];

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function LatexIndexPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const router = useRouter();

  const [docs, setDocs] = useState<LatexDocument[]>([]);
  const [role, setRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Declared with the rest of the state, ABOVE `visible` below. It used to
  // sit further down beside the rename state, which crashed the page: the
  // `docs.filter(...)` that reads it runs during render, before the
  // declaration, and `const` is in its temporal dead zone until then.
  // `tsc` cannot catch it -- the read is inside the arrow passed to
  // `filter`, and TypeScript has no way to know when a closure runs.
  const [query, setQuery] = useState("");

  const [editingMode, setEditingMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  const [newOpen, setNewOpen] = useState(false);
  // Open state only: the two-step plan/commit conversation, its busy flag
  // and its own error all live inside `ImportDropzone`. This page passes no
  // `documentId`, so the dialog can only ever CREATE a document here -- there
  // is nothing open to merge into -- and the only duplicate it can report is
  // a duplicate document NAME.
  const [importOpen, setImportOpen] = useState(false);

  // The duplicate document-NAME question, rendered through the same shared
  // dialog every other duplicate on this branch uses.
  // Carries the action to RETRY with the chosen name, not just the rows:
  // creating and renaming collide identically and answer the same dialog,
  // and hardwiring it to one of them would mean a second dialog for the
  // other.
  const [nameConflict, setNameConflict] = useState<{
    collisions: LatexCollision[];
    retry: (name: string) => Promise<void>;
  } | null>(null);
  const [nameBusy, setNameBusy] = useState(false);

  // `silent` skips the full-page loading skeleton. The skeleton branch below
  // unmounts the whole page -- including any error banner just set by a bulk
  // delete -- so a non-silent reload right after a partial failure destroys
  // the message telling the user it happened. Mirrors the papers page.
  //
  // `loadSeq` guards against out-of-order resolution the same way.
  const loadSeq = useRef(0);

  const load = useCallback(
    (opts: { silent?: boolean } = {}) => {
      const seq = ++loadSeq.current;
      if (!opts.silent) setLoading(true);
      if (!opts.silent) setError(null);
      Promise.all([listDocuments(projectId), getProject(projectId)])
        .then(([rows, detail]) => {
          if (seq !== loadSeq.current) return; // a newer load already won
          setDocs(rows);
          setRole(detail.my_role);
        })
        .catch((err) => {
          if (seq !== loadSeq.current) return;
          setError(errorText(err));
        })
        .finally(() => {
          if (seq === loadSeq.current) setLoading(false);
        });
    },
    [projectId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const canEdit = role !== null && CAN_EDIT.includes(role);

  // "Select all" means every row the user can actually act on -- which is
  // what the disabled checkboxes on view-only rows already say on screen.
  // Handing selectAll/isAllSelected the full doc list would select rows the
  // checkbox itself refuses to let the user check, guaranteeing a partial-
  // failure banner and making "all selected" unreachable by clicking.
  /** Name and main file -- the two things the row shows. */
  const searchable = (doc: LatexDocument) => [doc.name, doc.main_path];
  const visible = docs.filter((d) => matchesQuery(query, searchable(d)));

  // "Select all" means every row the user can actually act on -- which is
  // what the disabled checkboxes on view-only rows already say on screen --
  // AND that the search has left on screen. Handing selectAll/isAllSelected
  // the full doc list would select rows the checkbox itself refuses to let
  // the user check, guaranteeing a partial-failure banner and making "all
  // selected" unreachable by clicking.
  const deletableIds = visible
    .filter((d) => d.my_access === "editor")
    .map((d) => d.id);

  function changeQuery(next: string) {
    setQuery(next);
    // Selections that just left the screen go with it -- Delete must never
    // reach a row the user cannot see.
    const stillVisible = docs
      .filter((d) => matchesQuery(next, searchable(d)))
      .map((d) => d.id);
    setSelected((prev) => retainVisible(prev, stillVisible));
  }

  /**
   * Create, and turn a duplicate NAME into the same Keep both / Rename /
   * Cancel question a duplicate FILE already gets.
   *
   * `createDocument` rethrows `NameCollisionError` unchanged for exactly
   * this: the dialog is built from the server's own `suggestion`, which
   * `errorText` could name but not offer in one click. Without this the 409
   * fell through to the generic line and creating a second "Paper" was an
   * unactionable dead end. Every other failure keeps going to `error`.
   */
  async function createNamed(name: string) {
    setError(null);
    try {
      const doc = await createDocument(projectId, { name, source: STARTER });
      router.push(`/research/${projectId}/latex/${doc.id}`);
    } catch (err) {
      if (err instanceof NameCollisionError) {
        // One row, the same shape `import-dropzone.tsx` renders a duplicate
        // document name as -- same question, same server-computed
        // suggestion, so the same control rather than a second dialog.
        setNameConflict({
          collisions: [
            {
              path: err.takenName,
              existing: err.takenName,
              suggestion: err.suggestion,
            },
          ],
          retry: createNamed,
        });
        return;
      }
      setError(errorText(err));
    }
  }

  const [renaming, setRenaming] = useState<LatexDocument | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);

  async function renameTo(name: string) {
    const target = renaming;
    if (!target) return;
    setError(null);
    try {
      await renameDocument(target, name);
      setRenaming(null);
    } catch (err) {
      if (err instanceof NameCollisionError) {
        // The rename dialog closes and the conflict dialog takes over --
        // one question at a time, and the conflict dialog already asks
        // exactly this one with the server's suggestion in hand.
        setRenaming(null);
        setNameConflict({
          collisions: [
            {
              path: err.takenName,
              existing: err.takenName,
              suggestion: err.suggestion,
            },
          ],
          // Bound to THIS document, not to whatever `renaming` holds by the
          // time the user answers -- the dialog above has already cleared it.
          retry: (chosen) => renameDocument(target, chosen),
        });
        return;
      }
      setError(errorText(err));
    }
  }

  /** The retry path, free of the `renaming` state the dialog has released. */
  async function renameDocument(target: LatexDocument, name: string) {
    const updated = await patchDocument(projectId, target.id, { name });
    setDocs((prev) =>
      prev.map((d) => (d.id === updated.id ? { ...d, ...updated } : d)),
    );
  }

  async function confirmNameConflict(
    decisions: { path: string; new_path: string }[],
  ) {
    const pending = nameConflict;
    const chosen = decisions[0]?.new_path;
    if (!pending || !chosen) return;
    setNameBusy(true);
    try {
      // Straight back through the action that collided: the retry can
      // collide AGAIN (another member took that name while the dialog was
      // open), and that action replaces the dialog's row with the newer
      // collision.
      await pending.retry(chosen);
    } finally {
      setNameBusy(false);
    }
    // Only dismiss the entry this call opened -- if the retry collided
    // again, the action has already installed the newer one.
    setNameConflict((current) => (current === pending ? null : current));
  }

  // A LaTeX project is a whole file tree and there is no undo, so the two
  // actions on this page that cannot be taken back are the only ones that
  // ask -- and they ask through a real dialog, never `window.confirm`. See
  // `ConfirmDialog`: a page that fires several native dialogs gets them
  // SUPPRESSED by Chrome, after which `confirm()` returns false without
  // opening anything and the delete silently does nothing.
  const [pendingDelete, setPendingDelete] = useState<LatexDocument | null>(
    null,
  );
  const [pendingBulkDelete, setPendingBulkDelete] = useState(false);

  async function handleDelete(doc: LatexDocument) {
    setPendingDelete(null);
    setBusyId(doc.id);
    setError(null);
    try {
      await deleteDocument(projectId, doc.id);
      setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    } catch (err) {
      setError(errorText(err));
      load({ silent: true });
    } finally {
      setBusyId(null);
    }
  }

  async function handleBulkDelete() {
    setPendingBulkDelete(false);
    setBulkBusy(true);
    setBulkError(null);
    const ids = [...selected];
    const results = await Promise.allSettled(
      ids.map((id) => deleteDocument(projectId, id)),
    );
    const failed = ids.filter((_, i) => results[i].status === "rejected");
    setSelected(new Set(failed));
    if (failed.length > 0) {
      setBulkError(`${failed.length} of ${ids.length} could not be deleted.`);
    }
    setBulkBusy(false);
    // Re-fetched unconditionally: what just proved unreliable is precisely
    // this client's idea of what exists. Silent: a non-silent load flips
    // `loading` true, and the skeleton branch below unmounts the whole page
    // -- including the bulkError banner just set above -- which would erase
    // the failure message on exactly the page where a partial failure is
    // most likely (some rows are view-only and never eligible to delete).
    load({ silent: true });
  }

  async function handleExport(doc: LatexDocument) {
    setBusyId(doc.id);
    setError(null);
    try {
      await downloadExport(projectId, doc.id, doc.name);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusyId(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  return (
    <div>
      {/* Count and actions on one line, the search box on its own beneath
          them at full width -- see the papers page for why. */}
      <div className="mb-4 flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {docs.length === 0
              ? "No LaTeX projects yet"
              : query
                ? `${visible.length} of ${docs.length} projects`
                : `${docs.length} project${docs.length !== 1 ? "s" : ""}`}
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <BulkEditBar
              active={editingMode}
              count={selected.size}
              total={deletableIds.length}
              allSelected={isAllSelected(selected, deletableIds)}
              busy={bulkBusy}
              onEnter={() => setEditingMode(true)}
              onSelectAll={() => setSelected(selectAll(selected, deletableIds))}
              onClear={() => setSelected(clear())}
              onDelete={() => void handleBulkDelete()}
              onDone={() => {
                setEditingMode(false);
                // A selection that survives invisibly is a delete waiting to
                // hit the wrong rows.
                setSelected(clear());
              }}
            />
            {canEdit && (
              <Button onClick={() => setNewOpen(true)}>
                <Plus className="size-4" />
                New project
              </Button>
            )}
          </div>
        </div>
        {docs.length > 0 && (
          <SearchInput
            value={query}
            onChange={changeQuery}
            placeholder="Search projects…"
            label="Search LaTeX projects by name or main file"
          />
        )}
      </div>

      {error && (
        <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {bulkError && (
        <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {bulkError}
        </p>
      )}

      {docs.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-24 text-center">
          <FileCode2 className="size-8 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">
            {canEdit
              ? "Start a blank paper, or import a .zip from Overleaf."
              : "No LaTeX projects have been created yet."}
          </p>
        </div>
      ) : visible.length === 0 ? (
        /* A query that matches nothing needs saying: an empty list under a
           filled search box otherwise reads as the projects having gone. */
        <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          No projects match “{query}”.
        </p>
      ) : (
        <div className="space-y-2">
          {visible.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3"
            >
              {editingMode && (
                <input
                  type="checkbox"
                  checked={selected.has(doc.id)}
                  disabled={doc.my_access !== "editor"}
                  onChange={() => setSelected(toggle(selected, doc.id))}
                  aria-label={`Select ${doc.name}`}
                  title={
                    doc.my_access === "editor"
                      ? undefined
                      : "You need edit access to delete this project"
                  }
                  className="mt-1 size-4 shrink-0 disabled:opacity-40"
                />
              )}
              {/* The whole name is the link, but the row's buttons are not
                  inside it -- an <a> wrapping the actions would make Export
                  navigate as well as download. */}
              <Link
                href={`/research/${projectId}/latex/${doc.id}`}
                className="min-w-0 flex-1"
              >
                <p className="line-clamp-1 text-sm font-medium text-foreground">
                  {doc.name}
                </p>
                <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                  {doc.main_path}
                </p>
                <p className="mt-1.5 text-xs text-muted-foreground/60">
                  {doc.engine} · updated {fmtDate(doc.updated_at)}
                </p>
              </Link>

              <button
                onClick={() => void handleExport(doc)}
                disabled={busyId === doc.id}
                title="Download .zip"
                aria-label={`Download ${doc.name} as .zip`}
                className="shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
              >
                <Download className="size-4" />
              </button>

              {doc.my_access === "editor" && (
                <>
                  <button
                    onClick={() => setRenaming(doc)}
                    className="shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-muted hover:text-foreground"
                    aria-label={`Rename project: ${doc.name}`}
                    title="Rename"
                  >
                    <Pencil className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setPendingDelete(doc)}
                    disabled={busyId === doc.id}
                    title="Delete project"
                    aria-label={`Delete ${doc.name}`}
                    className="shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      <NewDocumentDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreateBlank={(name) => void createNamed(name)}
        onChooseImport={() => setImportOpen(true)}
      />

      <ImportDropzone
        open={importOpen}
        projectId={projectId}
        takenNames={docs.map((d) => d.name)}
        onClose={() => setImportOpen(false)}
        onDone={(result) => {
          setImportOpen(false);
          router.push(`/research/${projectId}/latex/${result.id}`);
        }}
      />

      <ConflictDialog
        open={nameConflict !== null}
        busy={nameBusy}
        title="That name is taken"
        description="This project already has a LaTeX project with that name. Keep both, or choose a different name."
        collisions={nameConflict?.collisions ?? []}
        taken={docs.map((d) => d.name)}
        onCancel={() => setNameConflict(null)}
        onConfirm={(decisions) => void confirmNameConflict(decisions)}
      />

      <RenameDialog
        open={renaming !== null}
        title="Rename LaTeX project"
        label="Name"
        initialValue={renaming?.name ?? ""}
        busy={renameBusy}
        onCancel={() => setRenaming(null)}
        onSubmit={(value) => {
          setRenameBusy(true);
          void renameTo(value).finally(() => setRenameBusy(false));
        }}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this LaTeX project?"
        description={`"${pendingDelete?.name ?? ""}" and all of its files will be deleted. This cannot be undone.`}
        confirmLabel="Delete"
        busy={busyId === pendingDelete?.id}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) void handleDelete(pendingDelete);
        }}
      />

      <ConfirmDialog
        open={pendingBulkDelete}
        title={`Delete ${selected.size} LaTeX project${selected.size !== 1 ? "s" : ""}?`}
        description="Every selected project and all of its files will be deleted. This cannot be undone."
        confirmLabel="Delete"
        busy={bulkBusy}
        onCancel={() => setPendingBulkDelete(false)}
        onConfirm={() => void handleBulkDelete()}
      />
    </div>
  );
}
