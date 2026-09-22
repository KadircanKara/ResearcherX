"use client";

import { useState } from "react";
import { ColorSwatches } from "@/components/project-header";
import { Button } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";
import { PROJECT_COLORS, type ProjectColor } from "@/lib/project-colors";
import { parseKeywords } from "@/lib/project-view";

export type NewProjectValues = {
  title: string;
  /** Trimmed; `""` when left blank. */
  description: string;
  keywords: string[];
  color: ProjectColor;
};

const FORM_ID = "new-project-form";

/**
 * The prototype's new-project dialog, creating a real project.
 *
 * `onCreate` does the request; a rejection keeps the dialog open with the
 * fields intact and a line saying it failed. The fields sit in a `<form>` so
 * Enter in a single-line field submits, and the footer's Create button points
 * at it with `form=` -- the form wraps only the fields, so the dialog keeps the
 * prototype's header / fields / footer layout.
 */
export function NewProjectDialog({
  open,
  onOpenChange,
  onCreate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (values: NewProjectValues) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [keywords, setKeywords] = useState("");
  const [color, setColor] = useState<ProjectColor>(PROJECT_COLORS[0]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setTitle("");
    setDescription("");
    setKeywords("");
    setColor(PROJECT_COLORS[0]);
    setSubmitting(false);
    setError(null);
  };

  const close = () => {
    onOpenChange(false);
    reset();
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onCreate({
        title: title.trim(),
        description: description.trim(),
        keywords: parseKeywords(keywords),
        color,
      });
      reset();
    } catch {
      setError("Failed to create project. Please try again.");
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>
            A project keeps its own papers, conversations and LaTeX documents.
          </DialogDescription>
        </DialogHeader>

        <form id={FORM_ID} onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="np-title">Title</Label>
            <Input
              id="np-title"
              autoFocus
              placeholder="e.g. Climate policy 2024"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="np-description">Description</Label>
            <Textarea
              id="np-description"
              rows={2}
              placeholder="Optional short description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="np-keywords">Topic keywords</Label>
            <Input
              id="np-keywords"
              placeholder="carbon, energy, policy (comma-separated)"
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label>Colour</Label>
            <ColorSwatches value={color} onChange={setColor} />
          </div>
          {error && (
            <p role="alert" className="text-[13px] text-destructive">
              {error}
            </p>
          )}
        </form>

        <DialogFooter>
          <Button variant="outline" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form={FORM_ID} disabled={!title.trim() || submitting}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
