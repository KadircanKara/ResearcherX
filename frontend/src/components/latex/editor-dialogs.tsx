"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * The LaTeX editor's three small dialogs, ported from the prototype's
 * `new-file-dialog`, `latex-rename-dialog` and `latex-delete-dialog`.
 *
 * Where the prototype closes on submit, these wait for the server: every
 * one of them can be refused (a bad path, a taken name), and the refusal is
 * shown in the dialog the user is still looking at, in the slot the
 * prototype used for its own client-side check.
 */

export function EditorNewFileDialog({
  open,
  initialValue,
  busy,
  error,
  onOpenChange,
  onCreate,
}: {
  open: boolean;
  /** Seeds the field, e.g. `chapters/` when opened from a folder's "+". */
  initialValue: string;
  busy: boolean;
  /** The server's own words for a refused path -- never a browser-side check. */
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onCreate: (path: string) => void;
}) {
  const [value, setValue] = useState(initialValue);

  useEffect(() => {
    if (open) setValue(initialValue);
  }, [open, initialValue]);

  // No path validation here, deliberately: `latex_paths.normalize_path` on
  // the backend is the one traversal guard, and a browser copy of its rules
  // would drift from it. An empty field is the only thing refused locally --
  // there is nothing to send.
  const trimmed = value.trim();

  return (
    <Dialog open={open} onOpenChange={(next) => onOpenChange(next)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New file</DialogTitle>
          <DialogDescription>Give the file a path, e.g. chapters/results.tex.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="new-file-path">Path</Label>
          <Input
            id="new-file-path"
            autoFocus
            value={value}
            placeholder="chapters/results.tex"
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && trimmed && !busy) onCreate(trimmed);
            }}
          />
          {error && <p className="text-[12px] text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!trimmed || busy} onClick={() => onCreate(trimmed)}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function EditorRenameDialog({
  open,
  currentName,
  busy,
  error,
  onOpenChange,
  onRename,
}: {
  open: boolean;
  currentName: string;
  busy: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onRename: (name: string) => void;
}) {
  const [value, setValue] = useState(currentName);

  useEffect(() => {
    if (open) setValue(currentName);
  }, [open, currentName]);

  const trimmed = value.trim();
  const disabled = !trimmed || trimmed === currentName || busy;

  return (
    <Dialog open={open} onOpenChange={(next) => onOpenChange(next)}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Rename document</DialogTitle>
          <DialogDescription>This only changes the document&apos;s name, not its files.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="latex-rename">Name</Label>
          <Input
            id="latex-rename"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !disabled) onRename(trimmed);
            }}
          />
          {error && <p className="text-[12px] text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={disabled} onClick={() => onRename(trimmed)}>
            Rename
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The only irreversible control in the editor, so the only one that asks --
 * through a real dialog, never `window.confirm`, which can silently return
 * false without opening.
 */
export function EditorDeleteDialog({
  open,
  docName,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  docName: string;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={(next) => onOpenChange(next)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete “{docName}”?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes the document and every file in it. This can&apos;t be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            onClick={onConfirm}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
