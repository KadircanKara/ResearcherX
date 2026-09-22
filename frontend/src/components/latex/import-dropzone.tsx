"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ImportZipDropStep } from "@/components/latex/import-zip-drop-step";
import { ImportZipPlanStep } from "@/components/latex/import-zip-plan-step";
import {
  commitImport,
  errorText,
  planImport,
  type LatexImportPlan,
  type LatexImportResult,
} from "@/lib/latex";
import { type ConflictState, initialState } from "@/lib/latex-conflicts";
import { commitBody, planReady } from "@/lib/latex-import";

interface ImportDropzoneProps {
  open: boolean;
  projectId: string;
  /**
   * The document the dialog was opened FROM. Present only inside a
   * workspace, and it is the whole reason the dialog merges: from the
   * projects list there is no open document to merge into, so the dialog
   * can only create one.
   */
  documentId?: string;
  /**
   * An archive the caller already has, so the dialog opens with it chosen.
   *
   * The tree's "Add files" control routes a `.zip` here rather than opening
   * an empty dialog the user would have to browse from again -- they picked
   * the file once already.
   */
  initialFile?: File | null;
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
 * Import is TWO requests and two steps, and the dialog owns both.
 *
 * "Plan import" uploads the archive once (`planImport`) and the plan step
 * shows everything the server says the user must be asked -- an undecidable
 * main file, a duplicate project name, colliding files -- without writing
 * anything; "Import" redeems the returned `staging_id` with the answers
 * (`commitImport`). The token is SINGLE USE, so a failed commit is never
 * retried against it: the plan is dropped, the dialog goes back to the drop
 * step with the server's message, and "Plan import" re-plans the same file.
 *
 * The flow lives here rather than in `use-latex-document` because the
 * projects LIST page offers import too and has no hook at all -- this
 * component is the one thing both surfaces share.
 */
export function ImportDropzone({
  open,
  projectId,
  documentId,
  initialFile = null,
  takenPaths = [],
  takenNames = [],
  onClose,
  onDone,
}: ImportDropzoneProps) {
  // Where this dialog is MOUNTED decides what it does, and the user is not
  // asked: inside a workspace it can only merge into the open project.
  const merge = Boolean(documentId);

  const [step, setStep] = useState<"drop" | "plan">("drop");
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<"plan" | "commit" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The staged plan, held between the two calls. Cleared whenever the token
  // it carries is spent, abandoned (Back) or the dialog closes -- never reused.
  const [plan, setPlan] = useState<LatexImportPlan | null>(null);
  // Never defaulted -- see the picker in `ImportZipPlanStep`.
  const [chosenMain, setChosenMain] = useState<string | null>(null);
  const [nameState, setNameState] = useState<ConflictState>(initialState());
  const [fileState, setFileState] = useState<ConflictState>(initialState());
  // Bumped on close, so a plan that resolves after the user closed the
  // dialog is dropped instead of reopening it on a stale question.
  const session = useRef(0);

  // Seeded from the caller's file, and keyed on `open` as well as the file
  // itself: reopening the dialog with the SAME archive must re-seed it,
  // which a dependency on the file alone would skip.
  useEffect(() => {
    if (open && initialFile) {
      setFile(initialFile);
      setName(stripExtension(initialFile.name));
    }
  }, [open, initialFile]);

  useEffect(() => {
    if (!open) {
      session.current += 1;
      setStep("drop");
      setFile(null);
      setName("");
      setBusy(null);
      setError(null);
      setPlan(null);
      setChosenMain(null);
      setNameState(initialState());
      setFileState(initialState());
    }
  }, [open]);

  function pickFile(next: File) {
    setFile(next);
    setName(stripExtension(next.name));
    setError(null);
  }

  const canPlan = file !== null && (merge || name.trim().length > 0);

  async function runPlan() {
    if (!file || !canPlan || busy) return;
    const mine = session.current;
    setBusy("plan");
    setError(null);
    try {
      const next = await planImport(
        projectId,
        file,
        merge ? { documentId } : { name: name.trim() }
      );
      if (session.current !== mine) return;
      setPlan(next);
      setChosenMain(null);
      setNameState(initialState());
      setFileState(initialState());
      setStep("plan");
    } catch (err) {
      if (session.current !== mine) return;
      setError(errorText(err));
    } finally {
      if (session.current === mine) setBusy(null);
    }
  }

  const ready =
    plan !== null &&
    planReady({ plan, chosenMain, nameState, fileState, takenNames, takenPaths });

  async function runCommit() {
    if (!plan || !ready || busy) return;
    setBusy("commit");
    setError(null);
    try {
      const result = await commitImport(
        projectId,
        commitBody({ plan, chosenMain, nameState, fileState, typedName: name, documentId })
      );
      onDone(result, plan.mode);
    } catch (err) {
      // The token is SINGLE USE and the commit consumed it, whatever the
      // outcome -- an expired (410) or unknown (404) token most obviously,
      // but a rejected decision too. Re-sending the same `staging_id` can
      // only ever fail again, so the plan is dropped and "Plan import"
      // re-uploads the file the user already picked.
      setError(errorText(err));
      setPlan(null);
      setStep("drop");
    } finally {
      setBusy(null);
    }
  }

  function back() {
    // Abandoned, not reused: the server evicts an unredeemed staging entry.
    setPlan(null);
    setStep("drop");
  }

  return (
    // A commit in flight cannot be dismissed: it writes whatever the dialog
    // does next, and closing would lose the navigation to what it created.
    <Dialog open={open} onOpenChange={(next) => !next && busy !== "commit" && onClose()}>
      {/* `max-h`/`overflow` only bite on a very long candidate list; the
          prototype's own lists are already bounded. */}
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{merge ? "Add a .zip to this project" : "Import a .zip"}</DialogTitle>
          <DialogDescription>
            {merge
              ? "Its files are unpacked into this project. LaTeX cannot read inside an archive, so a .zip left whole could never be referenced."
              : "Upload a LaTeX project exported from Overleaf or another editor."}
          </DialogDescription>
        </DialogHeader>

        {step === "plan" && plan ? (
          <ImportZipPlanStep
            plan={plan}
            chosenMain={chosenMain}
            onChooseMain={setChosenMain}
            nameState={nameState}
            setNameState={setNameState}
            fileState={fileState}
            setFileState={setFileState}
            takenNames={takenNames}
            takenPaths={takenPaths}
            busy={busy !== null}
          />
        ) : (
          <ImportZipDropStep
            mode={merge ? "merge" : "new"}
            fileName={file?.name ?? null}
            onFile={pickFile}
            name={name}
            onNameChange={setName}
            planning={busy === "plan"}
            canPlan={canPlan}
            onPlan={() => void runPlan()}
            error={error}
          />
        )}

        {step === "plan" && plan && (
          <DialogFooter>
            <Button variant="outline" disabled={busy !== null} onClick={back}>
              Back
            </Button>
            <Button disabled={!ready || busy !== null} onClick={() => void runCommit()}>
              {busy === "commit" && <Loader2 className="animate-spin" />}
              Import
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
