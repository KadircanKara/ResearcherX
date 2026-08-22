"use client";

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

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  /** The destructive action's own verb -- "Delete", not "OK". */
  confirmLabel: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * The confirmation every destructive action in this app uses.
 *
 * It replaces `window.confirm`, which is not merely unstyled but
 * UNRELIABLE: Chrome offers "Prevent this page from creating additional
 * dialogs" once a page has shown several native dialogs, and once that is
 * active `confirm()` returns FALSE without opening anything. The caller
 * sees a decline it cannot distinguish from a real one, so the delete
 * silently does nothing and no message explains why -- observed live on the
 * LaTeX projects list, which fires a native confirm from both its per-row
 * delete and its bulk delete. A reload clears the suppression, which is
 * exactly what makes the symptom look like a phantom.
 *
 * A React dialog cannot be suppressed by the browser, so the confirmation
 * either appears or the bug is in our code -- which is the property that
 * matters. Do not reintroduce `window.confirm` for a destructive action.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  busy = false,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && !busy && onCancel()}>
      {/* Same overflow discipline as every other dialog in this app: a
          flex column capped at the viewport with the body in its own
          scroller, so a long name can never push the footer out of reach. */}
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="break-words">{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={busy}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
