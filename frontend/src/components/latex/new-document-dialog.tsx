"use client";

import { useEffect, useState } from "react";
import { FileText, UploadCloud } from "lucide-react";
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

export function NewDocumentDialog({
  open,
  onClose,
  onCreateBlank,
  onChooseImport,
}: NewDocumentDialogProps) {
  const [choice, setChoice] = useState<Choice>(null);
  const [name, setName] = useState("");

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
          <div className="flex flex-col gap-2">
            <ChoiceCard
              icon={<FileText className="size-5" />}
              title="Blank project"
              detail="One main.tex with a minimal IEEEtran skeleton."
              onClick={() => setChoice("blank")}
            />
            <ChoiceCard
              icon={<UploadCloud className="size-5" />}
              title="Import .zip"
              detail="A LaTeX project exported from Overleaf or elsewhere."
              onClick={() => {
                onClose();
                onChooseImport();
              }}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">
              Project name
            </label>
            <Input
              autoFocus
              value={name}
              placeholder="paper"
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

function ChoiceCard({
  icon,
  title,
  detail,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-start gap-3 rounded-lg border border-input px-3 py-3 text-left transition-colors",
        "hover:border-primary/50 hover:bg-muted/60"
      )}
    >
      <span className="mt-0.5 text-muted-foreground">{icon}</span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-foreground">{title}</span>
        <span className="block text-xs text-muted-foreground">{detail}</span>
      </span>
    </button>
  );
}
