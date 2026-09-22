"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { TITLE_MAX } from "@/lib/paper-batch";
import { createPaper } from "@/lib/projects";

/**
 * Manual: a paper typed in by hand. The backend indexes its abstract and
 * body inside the same transaction that writes it, so it is searchable the
 * moment this resolves — or, with neither, has nothing to index at all.
 *
 * The prototype's Authors field is not here: the create endpoint has no
 * authors field, so whatever was typed into it would be silently dropped.
 */
export function AddPaperManualTab({
  projectId,
  onSaved,
  onDone,
  onBusyChange,
}: {
  projectId: string;
  onSaved: () => void;
  onDone: () => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [title, setTitle] = useState("");
  const [abstract, setAbstract] = useState("");
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    onBusyChange(submitting);
  }, [submitting, onBusyChange]);

  async function submit() {
    if (!title.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await createPaper(projectId, {
        title: title.trim(),
        abstract: abstract.trim() || null,
        body: body.trim() || null,
        source: "manual",
      });
      setSubmitting(false);
      onSaved();
      onDone();
    } catch {
      setError("Couldn't add this paper. Please try again.");
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="manual-title">Title</Label>
        <Input
          id="manual-title"
          value={title}
          maxLength={TITLE_MAX}
          disabled={submitting}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="manual-abstract">Abstract</Label>
        <Textarea
          id="manual-abstract"
          rows={3}
          value={abstract}
          disabled={submitting}
          onChange={(e) => setAbstract(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="manual-body">Body</Label>
        <Textarea
          id="manual-body"
          rows={4}
          value={body}
          disabled={submitting}
          onChange={(e) => setBody(e.target.value)}
        />
      </div>
      {error && <p className="text-[12px] text-destructive">{error}</p>}
      <div className="flex justify-end">
        <Button disabled={!title.trim() || submitting} onClick={() => void submit()}>
          {submitting ? "Adding…" : "Add paper"}
        </Button>
      </div>
    </div>
  );
}
