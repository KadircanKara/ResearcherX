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
import { cn } from "@/lib/utils";

interface ImportDropzoneProps {
  open: boolean;
  busy: boolean;
  error: string | null;
  /** Non-empty only after a 422 ambiguous_main; the user picks one. */
  candidates: string[];
  onClose: () => void;
  onImport: (zip: File, name: string, mainPath?: string) => void;
}

function stripExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? filename : filename.slice(0, dot);
}

export function ImportDropzone({
  open,
  busy,
  error,
  candidates,
  onClose,
  onImport,
}: ImportDropzoneProps) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [dragOver, setDragOver] = useState(false);
  // Left unselected on purpose: the picker only exists because the backend
  // refused to guess, so this component must not default it to
  // `candidates[0]` either -- that would be exactly the confident wrong
  // answer the 422 exists to avoid. The user has to make an active choice
  // before "Import" is even clickable.
  const [chosenMain, setChosenMain] = useState<string | null>(null);

  // Reset only when the dialog actually closes -- NOT when `candidates`
  // arrives. The candidate picker is a second render of the SAME open dialog
  // (the first `onImport` call came back ambiguous), and it has to resubmit
  // the same File the user already dropped, so `file`/`name` must survive
  // that transition.
  useEffect(() => {
    if (!open) {
      setFile(null);
      setName("");
      setDragOver(false);
      setChosenMain(null);
    }
  }, [open]);

  function pickFile(next: File | null) {
    setFile(next);
    if (next) setName(stripExtension(next.name));
  }

  function submit() {
    if (!file || !name.trim() || busy) return;
    if (candidates.length > 0) {
      if (!chosenMain) return;
      onImport(file, name.trim(), chosenMain);
      return;
    }
    onImport(file, name.trim());
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) pickFile(dropped);
  }

  const showPicker = candidates.length > 0;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-md">
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

        {showPicker ? (
          <div className="flex flex-col gap-1.5">
            {candidates.map((candidate) => (
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
                <span className="truncate font-mono text-xs">{candidate}</span>
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
                dragOver ? "border-primary bg-primary/5" : "border-input"
              )}
            >
              <UploadCloud className="size-6" />
              {file ? (
                <span className="font-medium text-foreground">{file.name}</span>
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

            {file && (
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted-foreground">
                  Document name
                </label>
                <Input value={name} onChange={(e) => setName(e.target.value)} />
              </div>
            )}
          </>
        )}

        {/*
          Every failure but the ambiguous-main one lands here verbatim: these
          are the user's own errors (too large, not a zip, encrypted,
          traversal, no main file) and the backend's message already names
          the real problem -- see `LatexRequestError.userMessage`.
        */}
        {error && <p className="text-sm text-destructive">{error}</p>}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={busy || !file || !name.trim() || (showPicker && !chosenMain)}
          >
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            Import
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
