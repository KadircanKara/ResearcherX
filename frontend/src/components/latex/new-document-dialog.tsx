"use client";

import { useEffect, useId, useState } from "react";
import { FileText, UploadCloud, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface NewDocumentDialogProps {
  open: boolean;
  onClose: () => void;
  /** Create a blank project: one main file seeded from the starter template. */
  onCreateBlank: (name: string) => void;
  /**
   * Hand off to the import flow. This dialog does NOT own the upload -- see
   * `ImportDropzone`, which already carries the drag/drop, the size and type
   * errors, and the ambiguous-main candidate picker. Duplicating any of that
   * here would give the two entry points different failure behaviour.
   */
  onChooseImport: () => void;
}

type Choice = "blank" | null;

/**
 * "New project" on the LaTeX list: a blank paper or an imported .zip.
 *
 * The prototype's button opens the import dialog directly and has no blank
 * option; the real app can create a blank project, so this chooser sits in
 * front of the import dialog, drawn in the prototype's dialog vocabulary.
 */
export function NewDocumentDialog({
  open,
  onClose,
  onCreateBlank,
  onChooseImport,
}: NewDocumentDialogProps) {
  const [choice, setChoice] = useState<Choice>(null);
  const [name, setName] = useState("");
  const nameId = useId();

  // Reset on close so the next open starts at the choice screen rather than
  // wherever the last one was abandoned.
  useEffect(() => {
    if (!open) {
      setChoice(null);
      setName("");
    }
  }, [open]);

  function submitBlank() {
    const trimmed = name.trim();
    if (!trimmed) return;
    onCreateBlank(trimmed);
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New LaTeX project</DialogTitle>
          <DialogDescription>
            Start from an empty paper, or bring one in from another editor.
          </DialogDescription>
        </DialogHeader>

        {choice === null ? (
          <div className="space-y-2">
            <ChoiceRow
              icon={FileText}
              title="Blank project"
              detail="One main.tex with a minimal IEEEtran skeleton."
              onClick={() => setChoice("blank")}
            />
            <ChoiceRow
              icon={UploadCloud}
              title="Import a .zip"
              detail="A LaTeX project exported from Overleaf or another editor."
              onClick={() => {
                onClose();
                onChooseImport();
              }}
            />
          </div>
        ) : (
          <div className="space-y-2">
            <Label htmlFor={nameId}>Name</Label>
            <Input
              id={nameId}
              autoFocus
              value={name}
              maxLength={200}
              placeholder="Project name"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitBlank();
              }}
            />
          </div>
        )}

        {choice !== null && (
          <DialogFooter>
            <Button variant="outline" onClick={() => setChoice(null)}>
              Back
            </Button>
            <Button onClick={submitBlank} disabled={!name.trim()}>
              Create
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ChoiceRow({
  icon: Icon,
  title,
  detail,
  onClick,
}: {
  icon: LucideIcon;
  title: string;
  detail: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-start gap-3 rounded-md border p-3 text-left transition-colors hover:bg-muted/40"
    >
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0">
        <span className="block text-[13px] font-medium">{title}</span>
        <span className="mt-0.5 block text-[12px] text-muted-foreground">{detail}</span>
      </span>
    </button>
  );
}
