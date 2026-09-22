"use client";

import { useEffect, useId, useState } from "react";
import { Loader2 } from "lucide-react";
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

interface RenameDialogProps {
  open: boolean;
  title?: string;
  description?: string;
  label?: string;
  /** The current name, which the field opens on. */
  initialValue: string;
  busy?: boolean;
  /** A failure from the save, rendered under the field. */
  error?: string | null;
  /** Called when the dialog is dismissed (Cancel, Escape, the corner ×). */
  onCancel?: () => void;
  /**
   * The prototype's API. When given, it is told `false` on dismissal AND
   * after `onSubmit`, as the prototype's dialog closes itself on save. A
   * caller whose save is async (and may fail into `error`) should pass
   * `onCancel` and close the dialog itself.
   */
  onOpenChange?: (open: boolean) => void;
  onSubmit: (value: string) => void;
}

/**
 * The rename prompt shared by conversations and LaTeX projects, drawn as the
 * prototype's rename dialog.
 *
 * One component rather than one per list: the two differ only in what
 * happens AFTER the save (a LaTeX project can collide on its name and hand
 * off to the conflict dialog; a conversation cannot), and that difference
 * belongs to the caller, not to the field it typed into.
 */
export function RenameDialog({
  open,
  title = "Rename",
  description,
  label = "Title",
  initialValue,
  busy = false,
  error = null,
  onCancel,
  onOpenChange,
  onSubmit,
}: RenameDialogProps) {
  const [value, setValue] = useState(initialValue);
  const fieldId = useId();

  // Re-seeded whenever the dialog opens on a DIFFERENT subject. Keyed on
  // `open` as well as the value, so reopening on the same row still starts
  // from the stored name rather than from whatever was half-typed and
  // abandoned last time.
  useEffect(() => {
    if (open) setValue(initialValue);
  }, [open, initialValue]);

  const trimmed = value.trim();
  const canSave = trimmed.length > 0 && !busy;

  function dismiss() {
    onCancel?.();
    onOpenChange?.(false);
  }

  function save() {
    if (!canSave) return;
    onSubmit(trimmed);
    onOpenChange?.(false);
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !busy && dismiss()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor={fieldId}>{label}</Label>
          <Input
            id={fieldId}
            value={value}
            autoFocus
            maxLength={200}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              // Enter saves: this dialog has one field, and reaching for the
              // mouse to commit a one-word edit is the whole friction.
              if (e.key === "Enter") save();
            }}
          />
          {error && <p className="break-words text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={dismiss} disabled={busy}>
            Cancel
          </Button>
          <Button disabled={!canSave} onClick={save}>
            {busy && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
