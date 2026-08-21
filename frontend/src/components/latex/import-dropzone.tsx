"use client";

import { useEffect, useState, type DragEvent } from "react";
import { Loader2, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ConflictDialog } from "@/components/latex/conflict-dialog";
import {
  commitImport,
  errorText,
  planImport,
  type LatexCollision,
  type LatexImportPlan,
  type LatexImportResult,
} from "@/lib/latex";
import { cn } from "@/lib/utils";

interface ImportDropzoneProps {
  open: boolean;
  projectId: string;
  /**
   * The document the dialog was opened FROM. Present only inside a
   * workspace, and it is the whole reason the "Add to this project" choice
   * exists: from the projects list there is no open document to merge into,
   * so the dialog can only create one.
   */
  documentId?: string;
  /** The open document's file paths -- what a MERGE's decisions must not
   * collide with. Empty from the list page, which never merges. */
  takenPaths?: string[];
  /** Existing document names in this project -- what a CREATE's name must
   * not collide with. */
  takenNames?: string[];
  onClose: () => void;
  /**
   * A committed import. `mode` is the SERVER's, taken from the plan: a merge
   * wrote into the document the caller already has open (refresh its tree),
   * a create made a new one (navigate to it).
   */
  onDone: (result: LatexImportResult, mode: "create" | "merge") => void;
}

function stripExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? filename : filename.slice(0, dot);
}

/**
 * Import is TWO requests, and the dialog owns both.
 *
 * `planImport` uploads the archive once and answers with everything the user
 * must be asked about -- colliding files, a duplicate document name, an
 * undecidable main file -- without writing anything; `commitImport` redeems
 * the returned `staging_id` with the answers. The token is SINGLE USE, so a
 * failed commit is never retried against it: the plan is dropped and the
 * next Import re-plans the same file.
 *
 * The flow lives here rather than in `use-latex-document` because the
 * projects LIST page offers import too and has no hook at all -- this
 * component is the one thing both surfaces share.
 */
export function ImportDropzone({
  open,
  projectId,
  documentId,
  takenPaths = [],
  takenNames = [],
  onClose,
  onDone,
}: ImportDropzoneProps) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [dragOver, setDragOver] = useState(false);
  // "merge" only means anything with a `documentId`; without one the choice
  // is not offered and this stays at its "create" default.
  const [target, setTarget] = useState<"merge" | "create">("merge");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The staged plan, held between the two calls. Cleared whenever the token
  // it carries is spent or the dialog closes -- never reused.
  const [plan, setPlan] = useState<LatexImportPlan | null>(null);
  // Left unselected on purpose: the picker only exists because the backend
  // refused to guess, so this component must not default it to
  // `candidates[0]` either -- that would be exactly the confident wrong
  // answer the plan's `ambiguous_main` exists to avoid.
  const [chosenMain, setChosenMain] = useState<string | null>(null);
  // The picker is answered with the Import button, not with the radio: a
  // conflict dialog swapping in the instant a radio is clicked reads as the
  // dialog losing the user's choice.
  const [mainConfirmed, setMainConfirmed] = useState(false);

  // Reset only when the dialog actually closes -- NOT when a plan arrives.
  // The picker and the conflict dialog are further renders of the SAME open
  // dialog, and they resubmit against the File the user already dropped, so
  // `file`/`name` must survive those transitions.
  useEffect(() => {
    if (!open) {
      setFile(null);
      setName("");
      setDragOver(false);
      setTarget("merge");
      setBusy(false);
      setError(null);
      setPlan(null);
      setChosenMain(null);
      setMainConfirmed(false);
    }
  }, [open]);

  const mergeChosen = Boolean(documentId) && target === "merge";
  const showPicker = plan !== null && plan.ambiguous_main !== null && !mainConfirmed;
  const conflictRows: LatexCollision[] = plan
    ? [
        // A duplicate document NAME is rendered as one more collision row --
        // same question ("this is taken, what should it be called?"), same
        // server-computed suggestion, so it gets the same control rather
        // than a second dialog of its own.
        ...(plan.name_collision
          ? [
              {
                path: plan.name_collision.name,
                existing: plan.name_collision.name,
                suggestion: plan.name_collision.suggestion,
              },
            ]
          : []),
        ...plan.collisions,
      ]
    : [];
  const showConflicts =
    plan !== null && conflictRows.length > 0 && (plan.ambiguous_main === null || mainConfirmed);

  async function runPlan(chosen: File) {
    setBusy(true);
    setError(null);
    try {
      const next = await planImport(
        projectId,
        chosen,
        mergeChosen ? { documentId } : { name: name.trim() }
      );
      setPlan(next);
      // Nothing to ask about: the plan is spent immediately rather than
      // making the user press Import a second time for no question.
      if (next.ambiguous_main === null && next.collisions.length === 0 && !next.name_collision) {
        await runCommit(next, []);
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function runCommit(
    staged: LatexImportPlan,
    decisions: { path: string; new_path: string }[],
    nameOverride?: string
  ) {
    setBusy(true);
    setError(null);
    try {
      const result = await commitImport(projectId, {
        staging_id: staged.staging_id,
        name: staged.mode === "create" ? (nameOverride ?? name.trim()) : undefined,
        main_path: chosenMain ?? undefined,
        document_id: staged.mode === "merge" ? documentId : undefined,
        decisions,
      });
      onDone(result, staged.mode);
    } catch (err) {
      setError(errorText(err));
      // The token is SINGLE USE and the commit consumed it, whatever the
      // outcome -- an expired (410) or unknown (404) token most obviously,
      // but a rejected decision too. Re-sending the same `staging_id` can
      // only ever fail again, so the plan is dropped and pressing Import
      // re-uploads and re-plans the file the user already picked.
      setPlan(null);
      setMainConfirmed(false);
    } finally {
      setBusy(false);
    }
  }

  function submit() {
    if (!file || busy) return;
    if (!mergeChosen && !name.trim()) return;
    if (plan === null) {
      void runPlan(file);
      return;
    }
    if (plan.ambiguous_main !== null && !mainConfirmed) {
      if (!chosenMain) return;
      setMainConfirmed(true);
      // Only the picker was outstanding -- commit straight away rather than
      // asking for one more click.
      if (conflictRows.length === 0) void runCommit(plan, []);
      return;
    }
    void runCommit(plan, []);
  }

  function confirmConflicts(decisions: { path: string; new_path: string }[]) {
    if (!plan) return;
    const nameRow = plan.name_collision;
    if (!nameRow) {
      void runCommit(plan, decisions);
      return;
    }
    // The name row is not a FILE decision and must not be sent as one -- the
    // commit route checks every decision's `path` against the archive's own
    // entries and 422s on one that is not there.
    const chosenName = decisions.find((d) => d.path === nameRow.name)?.new_path;
    void runCommit(
      plan,
      decisions.filter((d) => d.path !== nameRow.name),
      chosenName
    );
  }

  function pickFile(next: File | null) {
    setFile(next);
    // A new file invalidates whatever was staged for the old one.
    setPlan(null);
    setChosenMain(null);
    setMainConfirmed(false);
    setError(null);
    if (next) setName(stripExtension(next.name));
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) pickFile(dropped);
  }

  return (
    <>
      <Dialog
        open={open && !showConflicts}
        onOpenChange={(next) => !next && onClose()}
      >
        {/* `flex ... max-h-[90vh]` and the scroll container below, mirroring
          the paper dialog: the default `DialogContent` is a grid that grows
          with its content, so a tall body pushed the footer off the bottom
          of the viewport with no way to reach it. */}
        <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Import a .zip</DialogTitle>
            {showPicker ? (
              <DialogDescription>
                That archive has more than one main file. Which one should be
                compiled?
              </DialogDescription>
            ) : (
              <DialogDescription>
                Upload a LaTeX project exported from Overleaf or another editor.
              </DialogDescription>
            )}
          </DialogHeader>

          {/* `min-w-0` is what actually contains a long file name. A flex (or
            grid) item's automatic minimum size is its CONTENT, and an
            underscore-joined archive name has no break opportunity at all,
            so without this the body refuses to shrink and the name spills
            straight out past the dialog's edge. */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 overflow-y-auto">
            {showPicker ? (
              <div className="flex flex-col gap-1.5">
                {(plan?.ambiguous_main ?? []).map((candidate) => (
                  <label
                    key={candidate}
                    className="flex items-center gap-2 rounded-md border border-input px-2.5 py-1.5 text-sm hover:bg-muted/60"
                  >
                    <input
                      type="radio"
                      name="main-candidate"
                      value={candidate}
                      checked={chosenMain === candidate}
                      onChange={() => setChosenMain(candidate)}
                    />
                    <span
                      className="min-w-0 truncate font-mono text-xs"
                      title={candidate}
                    >
                      {candidate}
                    </span>
                  </label>
                ))}
              </div>
            ) : (
              <>
                <div
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragOver(true);
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  className={cn(
                    "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center text-sm text-muted-foreground transition-colors",
                    dragOver ? "border-primary bg-primary/5" : "border-input",
                  )}
                >
                  <UploadCloud className="size-6" />
                  {file ? (
                    /* Truncated with the full name on hover, exactly like the
                     paper upload rows -- wrapping instead would let one long
                     name resize the dialog under the user. */
                    <span
                      className="max-w-full truncate font-medium text-foreground"
                      title={file.name}
                    >
                      {file.name}
                    </span>
                  ) : (
                    <span>Drag a .zip here, or</span>
                  )}
                  <label className="cursor-pointer text-primary underline underline-offset-2">
                    browse
                    <input
                      type="file"
                      accept=".zip,application/zip"
                      className="hidden"
                      onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
                    />
                  </label>
                </div>

                {/* Offered ONLY inside a workspace. From the projects list
                    there is no open document to add to, and a choice with
                    one real option is not a choice. */}
                {file && documentId && (
                  <div className="flex flex-col gap-1.5">
                    <label className="flex items-center gap-2 rounded-md border border-input px-2.5 py-1.5 text-sm hover:bg-muted/60">
                      <input
                        type="radio"
                        name="import-target"
                        checked={target === "merge"}
                        onChange={() => {
                          setTarget("merge");
                          setPlan(null);
                          setMainConfirmed(false);
                        }}
                      />
                      <span>Add to this project</span>
                    </label>
                    <label className="flex items-center gap-2 rounded-md border border-input px-2.5 py-1.5 text-sm hover:bg-muted/60">
                      <input
                        type="radio"
                        name="import-target"
                        checked={target === "create"}
                        onChange={() => {
                          setTarget("create");
                          setPlan(null);
                          setMainConfirmed(false);
                        }}
                      />
                      <span>Create a new project</span>
                    </label>
                  </div>
                )}

                {/* A merge writes into a document that already has a name. */}
                {file && !mergeChosen && (
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-muted-foreground">
                      Document name
                    </label>
                    <Input
                      value={name}
                      onChange={(e) => {
                        setName(e.target.value);
                        // The name is part of what was planned (it is what
                        // `name_collision` was computed against), so editing
                        // it invalidates the staged plan.
                        setPlan(null);
                        setMainConfirmed(false);
                      }}
                    />
                  </div>
                )}
              </>
            )}

            {/*
            Every failure but the ones the plan reports as fields lands here
            verbatim: these are the user's own errors (too large, not a zip,
            encrypted, traversal, no main file, an expired upload) and the
            backend's message already names the real problem -- see
            `LatexRequestError.userMessage`.
          */}
            {error && (
              <p className="text-sm break-words text-destructive">{error}</p>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={submit}
              disabled={
                busy ||
                !file ||
                (!mergeChosen && !name.trim()) ||
                (showPicker && !chosenMain)
              }
            >
              {busy && <Loader2 className="size-3.5 animate-spin" />}
              Import
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConflictDialog
        open={open && showConflicts}
        busy={busy}
        title={plan?.mode === "merge" ? "Some files already exist" : "That name is taken"}
        description={
          plan?.mode === "merge"
            ? "These files are already in this project. Choose a name for each one the import should use."
            : "This project already has a document with that name. Choose a different one."
        }
        collisions={conflictRows}
        taken={plan?.mode === "merge" ? takenPaths : takenNames}
        onCancel={onClose}
        onConfirm={confirmConflicts}
      />
    </>
  );
}
