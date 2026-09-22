"use client";

import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  /** The destructive action's own verb -- "Delete", not "OK". */
  confirmLabel?: string;
  /** While true both buttons are disabled and the dialog cannot be dismissed. */
  busy?: boolean;
  /** Called when the dialog is dismissed (Cancel, Escape). */
  onCancel?: () => void;
  /**
   * The prototype's API. When given, it is told `false` on dismissal AND
   * after `onConfirm`, as Radix's `AlertDialogAction` closes on click. A
   * caller whose confirm is async and closes the dialog itself should pass
   * `onCancel` instead.
   */
  onOpenChange?: (open: boolean) => void;
  onConfirm: () => void;
}

/**
 * The confirmation every destructive action in this app uses, drawn as the
 * prototype's alert dialog.
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
  confirmLabel = "Delete",
  busy = false,
  onCancel,
  onOpenChange,
  onConfirm,
}: ConfirmDialogProps) {
  function dismiss() {
    onCancel?.();
    onOpenChange?.(false);
  }

  return (
    <AlertDialog open={open} onOpenChange={(next) => !next && !busy && dismiss()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription className="break-words">{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          {/* A plain Button, not AlertDialogAction: an action that closes on
              click could not keep the dialog up while an async delete runs. */}
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => {
              onConfirm();
              onOpenChange?.(false);
            }}
          >
            {busy && <Loader2 className="animate-spin" />}
            {confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
