"use client";

import { routes } from "@/lib/routes";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Download, FileCode2, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState, NoMatchState } from "@/components/ui/empty-state";
import { RenameDialog } from "@/components/ui/rename-dialog";
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
import { clear, retainVisible, selectAll, toggle } from "@/lib/selection";
import { matchesQuery } from "@/lib/search";
import { datePart, plural } from "@/lib/format";
import { STARTER } from "@/lib/latex-starter";
import type { Role } from "@/lib/types";

const CAN_EDIT: Role[] = ["owner", "member"];

/** Name and main file -- the two things the row shows. */
const searchable = (doc: LatexDocument) => [doc.name, doc.main_path];

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

  // The duplicate document-NAME question.
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

  const visible = docs.filter((d) => matchesQuery(query, searchable(d)));

  // "Select all" means every row the user can actually act on -- which is
  // what the disabled checkboxes on view-only rows already say on screen --
  // AND that the search has left on screen. Handing selectAll the full doc
  // list would select rows the checkbox itself refuses to let the user
  // check, guaranteeing a partial-failure banner.
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

  /** One taken-name row, built from the server's own suggestion. */
  function nameRow(err: NameCollisionError): LatexCollision[] {
    return [{ path: err.takenName, existing: err.takenName, suggestion: err.suggestion }];
  }

  /**
   * Create, and turn a duplicate NAME into the Keep both / Rename question.
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
      router.push(routes.latexDoc(projectId, doc.id));
    } catch (err) {
      if (err instanceof NameCollisionError) {
        setNameConflict({ collisions: nameRow(err), retry: createNamed });
        return;
      }
      setError(errorText(err));
    }
  }

  const [renaming, setRenaming] = useState<LatexDocument | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  async function renameTo(name: string) {
    const target = renaming;
    if (!target) return;
    setRenameError(null);
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
          collisions: nameRow(err),
          // Bound to THIS document, not to whatever `renaming` holds by the
          // time the user answers -- the dialog above has already cleared it.
          retry: (chosen) => renameDocument(target, chosen),
        });
        return;
      }
      setRenameError(errorText(err));
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
    } catch (err) {
      // A retry that fails for any OTHER reason closes the question and
      // says why on the page.
      setError(errorText(err));
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
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-[74px] animate-pulse rounded-md bg-muted" />
        ))}
      </div>
    );
  }

  const heading = query
    ? `${visible.length} of ${docs.length} projects`
    : docs.length
      ? plural(docs.length, "project")
      : "No LaTeX projects yet";

  return (
    <div className="fade-block space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">{heading}</h1>
        <div className="flex flex-wrap gap-2">
          <BulkEditBar
            editing={editingMode}
            selectedCount={selected.size}
            total={deletableIds.length}
            busy={bulkBusy}
            onStart={() => setEditingMode(true)}
            onSelectAll={() => setSelected(selectAll(selected, deletableIds))}
            onClear={() => setSelected(clear())}
            onDelete={() => setPendingBulkDelete(true)}
            onDone={() => {
              setEditingMode(false);
              // A selection that survives invisibly is a delete waiting to
              // hit the wrong rows.
              setSelected(clear());
            }}
          />
          {canEdit && (
            <Button size="sm" onClick={() => setNewOpen(true)}>
              <Plus className="size-4" /> New project
            </Button>
          )}
        </div>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
        <Input
          className="pl-9"
          placeholder="Search projects…"
          aria-label="Search LaTeX projects by name or main file"
          value={query}
          onChange={(e) => changeQuery(e.target.value)}
        />
      </div>

      {error && (
        <p className="break-words rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {bulkError && (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {bulkError}
        </p>
      )}

      {!docs.length ? (
        canEdit ? (
          <EmptyState
            icon={FileCode2}
            title="Start a blank paper, or import a .zip from Overleaf."
            body="Your LaTeX projects will appear here."
          />
        ) : (
          <EmptyState icon={FileCode2} title="No LaTeX projects have been created yet." />
        )
      ) : !visible.length ? (
        <NoMatchState query={query} noun="projects" />
      ) : (
        <div className="space-y-2">
          {visible.map((doc) => {
            const isEditor = doc.my_access === "editor";
            return (
              <div key={doc.id} className="group flex items-center gap-3 rounded-md border p-3">
                {editingMode && (
                  <Checkbox
                    checked={selected.has(doc.id)}
                    disabled={!isEditor}
                    aria-label={`Select ${doc.name}`}
                    title={isEditor ? undefined : "You need edit access to delete this project"}
                    onCheckedChange={() => setSelected(toggle(selected, doc.id))}
                  />
                )}
                {/* The name block is the link, but the row's buttons are not
                    inside it -- an <a> wrapping the actions would make
                    Download navigate as well as download. */}
                <Link className="min-w-0 flex-1" href={routes.latexDoc(projectId, doc.id)}>
                  <p className="truncate font-medium hover:underline">{doc.name}</p>
                  <p className="truncate font-mono text-[12px] text-muted-foreground">
                    {doc.main_path}
                  </p>
                  <p className="text-[12px] text-muted-foreground">
                    {doc.engine} · updated {datePart(doc.updated_at)}
                  </p>
                </Link>
                <div className="flex opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Download zip"
                    disabled={busyId === doc.id}
                    onClick={() => void handleExport(doc)}
                  >
                    <Download className="size-4" />
                  </Button>
                  {/* Rename and Delete need edit access on the document; a
                      viewer sees them, disabled, rather than a 403. */}
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Rename"
                    disabled={!isEditor}
                    onClick={() => {
                      setRenameError(null);
                      setRenaming(doc);
                    }}
                  >
                    <Pencil className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Delete"
                    disabled={!isEditor || busyId === doc.id}
                    onClick={() => setPendingDelete(doc)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </div>
            );
          })}
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
          router.push(routes.latexDoc(projectId, result.id));
        }}
      />

      <ConflictDialog
        open={nameConflict !== null}
        variant="name"
        busy={nameBusy}
        collisions={nameConflict?.collisions ?? []}
        taken={docs.map((d) => d.name)}
        onCancel={() => setNameConflict(null)}
        onConfirm={(decisions) => void confirmNameConflict(decisions)}
      />

      <RenameDialog
        open={renaming !== null}
        label="Name"
        initialValue={renaming?.name ?? ""}
        busy={renameBusy}
        error={renameError}
        onCancel={() => setRenaming(null)}
        onSubmit={(value) => {
          setRenameBusy(true);
          void renameTo(value).finally(() => setRenameBusy(false));
        }}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this LaTeX project?"
        description={`“${pendingDelete?.name ?? "Project"}” and all of its files will be deleted. This cannot be undone.`}
        busy={busyId !== null && busyId === pendingDelete?.id}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) void handleDelete(pendingDelete);
        }}
      />

      <ConfirmDialog
        open={pendingBulkDelete}
        title={`Delete ${plural(selected.size, "project")}?`}
        description="This cannot be undone."
        busy={bulkBusy}
        onCancel={() => setPendingBulkDelete(false)}
        onConfirm={() => void handleBulkDelete()}
      />
    </div>
  );
}
