"use client";

import { useCallback, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AddPaperUploadTab } from "@/components/papers/add-paper-upload-tab";

export function AddPaperDialog({
  projectId,
  open,
  onOpenChange,
  initialFiles,
  onSaved,
}: {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Files dropped on the library rail. Passed straight to the upload tab. */
  initialFiles?: File[];
  /** Something was written: the page re-reads the library. */
  onSaved: () => void;
}) {
  // Whether an extraction or upload is in flight. The dialog cannot be
  // dismissed while it is: closing unmounts the tab, and an unmounted batch
  // can neither finish its writes nor clean up after a failed one.
  const [busy, setBusy] = useState(false);

  const reportBusy = useCallback((b: boolean) => setBusy(b), []);

  /** The tab finished its work and asks to close: not subject to the guard. */
  function close() {
    setBusy(false);
    onOpenChange(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && busy) return;
        onOpenChange(next);
      }}
    >
      {/* max-h + scroll only engage past the viewport: a 20-file batch
          would otherwise push the Add button off screen. */}
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add papers</DialogTitle>
          <DialogDescription>
            Upload PDFs. Each one is read, indexed and ready to chat about.
          </DialogDescription>
        </DialogHeader>
        <AddPaperUploadTab
          projectId={projectId}
          initialFiles={initialFiles}
          onSaved={onSaved}
          onDone={close}
          onBusyChange={reportBusy}
        />
      </DialogContent>
    </Dialog>
  );
}
