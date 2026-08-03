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
import { PaperManualScreen, TITLE_MAX, type PaperFields } from "@/components/paper-manual-screen";
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
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = onOpenChange ?? setUncontrolledOpen;

  const [method, setMethod] = useState<PaperMethod>("upload");
  const [fields, setFields] = useState<PaperFields>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Content is only editable on papers the user typed in by hand.
  const readOnlyContent = isEdit && paper!.source !== "manual";

  useEffect(() => {
    if (!open) return;
    if (paper) {
      setMethod("manual");
      setFields({
        title: paper.title,
        abstract: paper.abstract ?? "",
        body: paper.body ?? "",
      });
    } else {
      setMethod("upload");
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
    ) : (
      <p className="py-8 text-center text-sm text-muted-foreground">
        {method === "upload" ? "Upload" : "Link"} screen lands in the next task.
      </p>
    );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {children && <DialogTrigger render={children}></DialogTrigger>}
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Paper" : "Add Paper"}</DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pb-1">
          {!isEdit && (
            <MethodSelector value={method} onChange={setMethod} disabled={submitting} />
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
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
