"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface RenameDialogProps {
  open: boolean;
  title: string;
  label: string;
  /** The current name, which the field opens on and selects. */
  initialValue: string;
  busy?: boolean;
  /** A failure from the save, rendered under the field. */
  error?: string | null;
  onCancel: () => void;
  onSubmit: (value: string) => void;
}

/**
 * The rename prompt shared by conversations and LaTeX projects.
 *
 * One component rather than one per list: the two differ only in what
 * happens AFTER the save (a LaTeX project can collide on its name and hand
 * off to the conflict dialog; a conversation cannot), and that difference
 * belongs to the caller, not to the field it typed into.
 */
export function RenameDialog({
  open,
  title,
  label,
  initialValue,
  busy = false,
  error = null,
  onCancel,
  onSubmit,
}: RenameDialogProps) {
  const [value, setValue] = useState(initialValue);

  // Re-seeded whenever the dialog opens on a DIFFERENT subject. Keyed on
  // `open` as well as the value, so reopening on the same row still starts
  // from the stored name rather than from whatever was half-typed and
  // abandoned last time.
  useEffect(() => {
    if (open) setValue(initialValue);
  }, [open, initialValue]);

  const trimmed = value.trim();
  const canSave = trimmed.length > 0 && !busy;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !busy && onCancel()}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="flex min-w-0 flex-col gap-1">
          <label className="text-xs font-medium text-muted-foreground">
            {label}
          </label>
          <Input
            autoFocus
            value={value}
            maxLength={200}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              // Enter saves: this dialog has one field, and reaching for the
              // mouse to commit a one-word edit is the whole friction.
              if (e.key === "Enter" && canSave) onSubmit(trimmed);
            }}
          />
          {error && (
            <p className="text-sm break-words text-destructive">{error}</p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={() => onSubmit(trimmed)} disabled={!canSave}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
