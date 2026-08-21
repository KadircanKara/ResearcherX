"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Download, FileCode2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { ConflictDialog } from "@/components/latex/conflict-dialog";
import { ImportDropzone } from "@/components/latex/import-dropzone";
import { NewDocumentDialog } from "@/components/latex/new-document-dialog";
import {
  createDocument,
  deleteDocument,
  downloadExport,
  listDocuments,
  errorText,
  NameCollisionError,
  type LatexCollision,
  type LatexDocument,
} from "@/lib/latex";
import { getProject } from "@/lib/projects";
import { clear, isAllSelected, selectAll, toggle } from "@/lib/selection";
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
  const [nameConflict, setNameConflict] = useState<{ collisions: LatexCollision[] } | null>(null);
  const [nameBusy, setNameBusy] = useState(false);

  // `silent` skips the full-page loading skeleton. The skeleton branch below
  // unmounts the whole page -- including any error banner just set by a bulk
  // delete -- so a non-silent reload right after a partial failure destroys
  // the message telling the user it happened. Mirrors the papers page.
  //
  // `loadSeq` guards against out-of-order resolution the same way.
  const loadSeq = useRef(0);

  const load = useCallback((opts: { silent?: boolean } = {}) => {
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
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const canEdit = role !== null && CAN_EDIT.includes(role);

  // "Select all" means every row the user can actually act on -- which is
  // what the disabled checkboxes on view-only rows already say on screen.
  // Handing selectAll/isAllSelected the full doc list would select rows the
  // checkbox itself refuses to let the user check, guaranteeing a partial-
  // failure banner and making "all selected" unreachable by clicking.
  const deletableIds = docs.filter((d) => d.my_access === "editor").map((d) => d.id);

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
            { path: err.takenName, existing: err.takenName, suggestion: err.suggestion },
          ],
        });
        return;
      }
      setError(errorText(err));
    }
  }

  async function confirmNameConflict(decisions: { path: string; new_path: string }[]) {
    const pending = nameConflict;
    const chosen = decisions[0]?.new_path;
    if (!pending || !chosen) return;
    setNameBusy(true);
    try {
      // Straight back through `createNamed`: the retry can collide AGAIN
      // (another member took that name while the dialog was open), and
      // `createNamed` replaces the dialog's row with the new collision.
      await createNamed(chosen);
    } finally {
      setNameBusy(false);
    }
    // Only dismiss the entry this call opened -- if the retry collided
    // again, `createNamed` has already installed the newer one.
    setNameConflict((current) => (current === pending ? null : current));
  }

  async function handleDelete(doc: LatexDocument) {
    // A LaTeX project is a whole file tree and there is no undo -- the one
    // action on this page that cannot be taken back gets an explicit
    // confirmation, unlike export.
    if (!window.confirm(`Delete "${doc.name}" and all of its files? This cannot be undone.`)) {
      return;
    }
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
    if (!window.confirm(`Delete ${selected.size} project${selected.size !== 1 ? "s" : ""} and all of their files? This cannot be undone.`)) {
      return;
    }
    setBulkBusy(true);
    setBulkError(null);
    const ids = [...selected];
    const results = await Promise.allSettled(
      ids.map((id) => deleteDocument(projectId, id))
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
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {docs.length === 0
            ? "No LaTeX projects yet"
            : `${docs.length} project${docs.length !== 1 ? "s" : ""}`}
        </p>
        <div className="flex items-center gap-2">
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
            <Button size="sm" onClick={() => setNewOpen(true)}>
              <Plus className="mr-1.5 size-3.5" />
              New project
            </Button>
          )}
        </div>
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
      ) : (
        <div className="space-y-2">
          {docs.map((doc) => (
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
                  title={doc.my_access === "editor" ? undefined : "You need edit access to delete this project"}
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
                <p className="line-clamp-1 text-sm font-medium text-foreground">{doc.name}</p>
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
                <button
                  onClick={() => void handleDelete(doc)}
                  disabled={busyId === doc.id}
                  title="Delete project"
                  aria-label={`Delete ${doc.name}`}
                  className="shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                >
                  <Trash2 className="size-4" />
                </button>
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
    </div>
  );
}
