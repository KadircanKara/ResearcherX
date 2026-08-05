"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { PaperLinkScreen } from "@/components/paper-link-screen";
import { PaperManualScreen, type PaperFields } from "@/components/paper-manual-screen";
import { TITLE_MAX } from "@/components/paper-row-fields";
import { PaperUploadScreen } from "@/components/paper-upload-screen";
import { createPaper, patchPaper } from "@/lib/projects";
import type { Paper } from "@/lib/types";

export type PaperMethod = "upload" | "link" | "manual";

const METHODS: { key: PaperMethod; label: string }[] = [
  { key: "upload", label: "Upload" },
  { key: "link", label: "Link" },
  { key: "manual", label: "Manual" },
];

const EMPTY: PaperFields = { title: "", abstract: "", body: "" };

function MethodSelector({
  value,
  onChange,
  disabled,
}: {
  value: PaperMethod;
  onChange: (m: PaperMethod) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex rounded-lg border border-border p-[3px]">
      {METHODS.map((m) => (
        <button
          key={m.key}
          type="button"
          disabled={disabled}
          onClick={() => onChange(m.key)}
          className={
            "flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 " +
            (value === m.key
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground")
          }
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

export function PaperDialog({
  projectId,
  onSaved,
  paper,
  open: controlledOpen,
  onOpenChange,
  children,
}: {
  projectId: string;
  onSaved: () => void;
  paper?: Paper;
  open?: boolean;
  onOpenChange?: (o: boolean) => void;
  children?: React.ReactElement;
}) {
  const isEdit = !!paper;
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  // One `isControlled` flag drives BOTH the getter and the setter. Falling back
  // independently (`controlledOpen ?? uncontrolledOpen` paired with
  // `onOpenChange ?? setUncontrolledOpen`) breaks the half-supplied cases:
  // `open` alone sticks the dialog open, `onOpenChange` alone never opens it.
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : uncontrolledOpen;
  const setOpen = (o: boolean) => {
    if (!isControlled) setUncontrolledOpen(o);
    onOpenChange?.(o);
  };

  // Only meaningful in add-mode; edit-mode always renders the manual form,
  // derived below rather than synced via effect (a synced value like
  // `useState("upload")` + `useEffect(() => setMethod("manual"), ...)` is
  // still "upload" for the render that mounts the dialog, since the effect
  // runs after that first commit — Edit Paper would flash the upload dropzone
  // for one frame before flipping to the form).
  const [methodState, setMethodState] = useState<PaperMethod>("upload");
  const method: PaperMethod = isEdit ? "manual" : methodState;
  const [fields, setFields] = useState<PaperFields>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Mirrors PaperUploadScreen's own extracting/saving state, which the parent
  // otherwise has no visibility into — used to lock the method selector and
  // the dialog's own dismiss gestures while a batch is in flight.
  const [screenBusy, setScreenBusy] = useState(false);

  // Native dismiss gestures (Escape, backdrop click, the built-in close
  // button) go through here. An in-flight upload batch must not be dismissed
  // this way — PaperUploadScreen's own Cancel/auto-close paths call
  // `() => setOpen(false)` directly and stay unguarded.
  const handleOpenChange = (o: boolean) => {
    if (!o && screenBusy) return;
    setOpen(o);
  };

  // Content is only editable on papers the user typed in by hand.
  const readOnlyContent = isEdit && paper!.source !== "manual";

  useEffect(() => {
    if (!open) return;
    if (paper) {
      setFields({
        title: paper.title,
        abstract: paper.abstract ?? "",
        body: paper.body ?? "",
      });
    } else {
      setMethodState("upload");
      setFields(EMPTY);
    }
    setError(null);
  }, [open, paper]);

  async function handleSave() {
    if (!fields.title.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      if (isEdit) {
        const payload: { title: string; abstract?: string | null; body?: string | null } = {
          title: fields.title.trim(),
        };
        if (!readOnlyContent) {
          payload.abstract = fields.abstract.trim() || null;
          payload.body = fields.body.trim() || null;
        }
        await patchPaper(projectId, paper!.id, payload);
      } else {
        await createPaper(projectId, {
          title: fields.title.trim(),
          abstract: fields.abstract.trim() || null,
          body: fields.body.trim() || null,
          source: "manual",
        });
      }
      setOpen(false);
      onSaved();
    } catch {
      setError("Failed to save paper. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const body =
    method === "manual" ? (
      <PaperManualScreen
        value={fields}
        onChange={setFields}
        disabled={submitting}
        readOnlyContent={readOnlyContent}
      />
    ) : method === "upload" ? (
      <PaperUploadScreen
        projectId={projectId}
        onSaved={onSaved}
        onClose={() => setOpen(false)}
        onBusyChange={setScreenBusy}
      />
    ) : (
      <PaperLinkScreen
        projectId={projectId}
        onSaved={onSaved}
        onClose={() => setOpen(false)}
        onBusyChange={setScreenBusy}
      />
    );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {children && <DialogTrigger render={children}></DialogTrigger>}
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Paper" : "Add Paper"}</DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pb-1">
          {!isEdit && (
            <MethodSelector
              value={method}
              onChange={setMethodState}
              disabled={submitting || screenBusy}
            />
          )}

          {body}

          {error && <p className="text-xs text-destructive">{error}</p>}

          {method === "manual" && (
            <div className="flex gap-2">
              <Button
                className="flex-1"
                onClick={handleSave}
                disabled={!fields.title.trim() || fields.title.length > TITLE_MAX || submitting}
              >
                {submitting ? "Saving…" : isEdit ? "Save Changes" : "Add Paper"}
              </Button>
              <Button variant="ghost" onClick={() => setOpen(false)} disabled={submitting}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
