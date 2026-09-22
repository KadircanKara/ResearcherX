"use client";

import { useCallback, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AddPaperManualTab } from "@/components/papers/add-paper-manual-tab";
import { AddPaperUploadTab } from "@/components/papers/add-paper-upload-tab";
import { AddPaperUrlTab } from "@/components/papers/add-paper-url-tab";

type AddTab = "upload" | "url" | "manual";

const IDLE: Record<AddTab, boolean> = { upload: false, url: false, manual: false };

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
  const [tab, setTab] = useState<AddTab>("upload");
  // Which tabs have work in flight (an extraction, an upload, a fetch). The
  // dialog cannot be dismissed while any does: closing unmounts the tab,
  // and an unmounted batch can neither finish its writes nor clean up after
  // a failed one.
  const [busy, setBusy] = useState<Record<AddTab, boolean>>(IDLE);
  const anyBusy = busy.upload || busy.url || busy.manual;

  const reportUpload = useCallback(
    (b: boolean) => setBusy((prev) => (prev.upload === b ? prev : { ...prev, upload: b })),
    []
  );
  const reportUrl = useCallback(
    (b: boolean) => setBusy((prev) => (prev.url === b ? prev : { ...prev, url: b })),
    []
  );
  const reportManual = useCallback(
    (b: boolean) => setBusy((prev) => (prev.manual === b ? prev : { ...prev, manual: b })),
    []
  );

  /** A tab finished its work and asks to close: not subject to the guard. */
  function close() {
    setBusy(IDLE);
    setTab("upload");
    onOpenChange(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && anyBusy) return;
        onOpenChange(next);
        if (!next) setTab("upload");
      }}
    >
      {/* max-h + scroll only engage past the viewport: a 20-file batch
          would otherwise push the Add button off screen. */}
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add papers</DialogTitle>
          <DialogDescription>
            Upload PDFs, link to a paper elsewhere, or enter one by hand.
          </DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={(next) => setTab(next as AddTab)}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="upload">Upload</TabsTrigger>
            <TabsTrigger value="url">URL</TabsTrigger>
            <TabsTrigger value="manual">Manual</TabsTrigger>
          </TabsList>
          {/* keepMounted: a tab with a batch in flight keeps running while the
              reader glances at another one, instead of being unmounted
              mid-upload. */}
          <TabsContent value="upload" keepMounted>
            <AddPaperUploadTab
              projectId={projectId}
              initialFiles={initialFiles}
              onSaved={onSaved}
              onDone={close}
              onBusyChange={reportUpload}
            />
          </TabsContent>
          <TabsContent value="url" keepMounted>
            <AddPaperUrlTab
              projectId={projectId}
              onSaved={onSaved}
              onDone={close}
              onBusyChange={reportUrl}
            />
          </TabsContent>
          <TabsContent value="manual" keepMounted>
            <AddPaperManualTab
              projectId={projectId}
              onSaved={onSaved}
              onDone={close}
              onBusyChange={reportManual}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
