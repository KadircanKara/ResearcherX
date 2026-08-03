"use client";

import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  createPaper,
  deletePaper,
  ingestPaper,
  ingestPaperFromUrl,
  suggestTitle,
  suggestTitleFromUrl,
} from "@/lib/projects";

interface AddPaperDialogProps {
  projectId: string;
  onAdded: () => void;
  children: React.ReactElement;
}

const TITLE_MAX = 150;
const TITLE_WARN = 120;

function FieldLabel({ label }: { label: string }) {
  return <p className="text-xs font-medium text-muted-foreground">{label}</p>;
}

function CharCounter({ value }: { value: string }) {
  if (value.length <= TITLE_WARN) return null;
  return (
    <p className="text-right text-xs text-muted-foreground">
      {TITLE_MAX - value.length} chars left
    </p>
  );
}

export function AddPaperDialog({ projectId, onAdded, children }: AddPaperDialogProps) {
  const [open, setOpen] = useState(false);

  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [abstract, setAbstract] = useState("");
  const [body, setBody] = useState("");

  const [extracting, setExtracting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paywalled, setPaywalled] = useState(false);

  const fileRef = useRef<HTMLInputElement>(null);

  function reset() {
    setFile(null);
    setUrl("");
    setTitle("");
    setAbstract("");
    setBody("");
    setExtracting(false);
    setError(null);
    setPaywalled(false);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function onFileSelected(f: File) {
    setFile(f);
    setTitle(f.name.replace(/\.pdf$/i, "").slice(0, TITLE_MAX));
    setAbstract("");
    setBody("");
    setError(null);
    setPaywalled(false);
    setExtracting(true);
    try {
      const bytes = await f.arrayBuffer();
      const { title: t, abstract: ab, body: bo } = await suggestTitle(projectId, bytes);
      if (t) setTitle(t.slice(0, TITLE_MAX));
      if (ab) setAbstract(ab);
      if (bo) setBody(bo);
    } catch {
      // fail-open: filename stays as title
    } finally {
      setExtracting(false);
    }
  }

  async function extractFromUrl() {
    if (!url.trim() || extracting) return;
    setExtracting(true);
    setError(null);
    try {
      const { title: t, abstract: ab, requires_manual } = await suggestTitleFromUrl(
        projectId,
        url.trim()
      );
      if (!requires_manual && t) {
        setTitle(t.slice(0, TITLE_MAX));
        if (ab) setAbstract(ab);
      } else if (requires_manual) {
        setError("Couldn't extract metadata from this URL — fill in the fields manually.");
      }
    } catch {
      setError("URL extraction failed. Fill in the fields manually.");
    } finally {
      setExtracting(false);
    }
  }

  async function handleSubmit() {
    if (!title.trim() || submitting || extracting) return;
    setSubmitting(true);
    setError(null);
    setPaywalled(false);

    const useFile = !!file;
    const useUrl = !useFile && !!url.trim();

    try {
      const paper = await createPaper(projectId, {
        title: title.trim(),
        abstract: abstract.trim() || null,
        body: body.trim() || null,
        pdf_url: url.trim() || null,
        source: "manual",
      });

      if (useFile) {
        try {
          const bytes = await file!.arrayBuffer();
          await ingestPaper(projectId, paper.id, bytes);
          setOpen(false);
          reset();
          onAdded();
        } catch {
          await deletePaper(projectId, paper.id).catch(() => {});
          setError("Upload failed. Please try again.");
        }
      } else if (useUrl) {
        try {
          await ingestPaperFromUrl(projectId, paper.id, url.trim());
          setOpen(false);
          reset();
          onAdded();
        } catch (e) {
          await deletePaper(projectId, paper.id).catch(() => {});
          if (e instanceof Error && (e as Error & { paywalled?: boolean }).paywalled) {
            setPaywalled(true);
          } else {
            setError("Failed to fetch paper. Please try again.");
          }
        }
      } else {
        // text-only
        setOpen(false);
        reset();
        onAdded();
      }
    } catch {
      setError("Failed to save paper. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger render={children as React.ReactElement}></DialogTrigger>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add Paper</DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pb-1">
          {/* Row 1: upload + URL */}
          <div className="flex gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onFileSelected(f);
              }}
            />
            <Button
              type="button"
              variant="outline"
              className="w-36 shrink-0 truncate"
              onClick={() => fileRef.current?.click()}
            >
              <Upload className="mr-1.5 size-3.5 shrink-0" />
              <span className="truncate">{file ? file.name : "Upload PDF"}</span>
            </Button>

            <div className="relative flex flex-1 items-center">
              <Input
                placeholder="https://arxiv.org/abs/…"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") extractFromUrl(); }}
                className="flex-1 pr-32"
              />
              <div className="absolute right-0 flex h-full items-center">
                <div className="h-4 w-px bg-zinc-300 dark:bg-zinc-600" />
                <button
                  type="button"
                  disabled={!url.trim() || extracting}
                  onClick={extractFromUrl}
                  className="rounded px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                >
                  {extracting ? "Extracting…" : "Extract Fields"}
                </button>
              </div>
            </div>
          </div>

          {/* Fields */}
          <div className="space-y-1">
            <FieldLabel label="Title" />
            <Input
              placeholder="Paper title"
              value={title}
              maxLength={TITLE_MAX}
              disabled={extracting}
              onChange={(e) => setTitle(e.target.value)}
            />
            <CharCounter value={title} />
          </div>

          <div className="space-y-1">
            <FieldLabel label="Abstract" />
            <textarea
              value={abstract}
              onChange={(e) => setAbstract(e.target.value)}
              placeholder="Abstract…"
              disabled={extracting}
              rows={3}
              className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div className="space-y-1">
            <FieldLabel label="Body" />
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Paper body text…"
              disabled={extracting}
              rows={6}
              className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          {paywalled && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
              <p className="font-medium text-destructive">
                Paywalled — no open-access version found.
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Upload the PDF directly to index it.
              </p>
            </div>
          )}

          {error && <p className="text-xs text-destructive">{error}</p>}

          <div className="flex gap-2">
            <Button
              className="flex-1"
              onClick={handleSubmit}
              disabled={!title.trim() || submitting || extracting}
            >
              {submitting ? "Saving…" : "Add Paper"}
            </Button>
            <Button variant="ghost" onClick={() => { setOpen(false); reset(); }}>
              Cancel
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
