"use client";

import { useRef, useState } from "react";
import { Link as LinkIcon, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  createPaper,
  ingestPaper,
  ingestPaperFromUrl,
} from "@/lib/projects";

interface AddPaperDialogProps {
  projectId: string;
  onAdded: () => void;
  children: React.ReactElement;
}

export function AddPaperDialog({
  projectId,
  onAdded,
  children,
}: AddPaperDialogProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<string>("pdf");

  // PDF tab
  const [file, setFile] = useState<File | null>(null);
  const [pdfTitle, setPdfTitle] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // URL tab
  const [url, setUrl] = useState("");
  const [urlTitle, setUrlTitle] = useState("");

  // Shared
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paywalled, setPaywalled] = useState(false);

  function reset() {
    setFile(null);
    setPdfTitle("");
    setUrl("");
    setUrlTitle("");
    setError(null);
    setPaywalled(false);
    setTab("pdf");
    if (fileRef.current) fileRef.current.value = "";
  }

  async function handlePdfSubmit() {
    if (!file || !pdfTitle.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const paper = await createPaper(projectId, { title: pdfTitle.trim() });
      const bytes = await file.arrayBuffer();
      await ingestPaper(projectId, paper.id, bytes);
      setOpen(false);
      reset();
      onAdded();
    } catch {
      setError("Upload failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleUrlSubmit() {
    if (!url.trim() || !urlTitle.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    setPaywalled(false);
    try {
      const paper = await createPaper(projectId, {
        title: urlTitle.trim(),
        pdf_url: url.trim(),
      });
      try {
        await ingestPaperFromUrl(projectId, paper.id, url.trim());
        setOpen(false);
        reset();
        onAdded();
      } catch (e) {
        if (e instanceof Error && (e as Error & { paywalled?: boolean }).paywalled) {
          setPaywalled(true);
          onAdded();   // paper record exists; refresh list now
        } else {
          setError("Failed to fetch paper. Please try again.");
        }
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
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add Paper</DialogTitle>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={(v) => {
            if (v) setTab(v);
            setError(null);
            setPaywalled(false);
          }}
        >
          <TabsList className="w-full">
            <TabsTrigger value="pdf" className="flex-1 gap-1.5">
              <Upload className="size-3.5" />
              Upload PDF
            </TabsTrigger>
            <TabsTrigger value="url" className="flex-1 gap-1.5">
              <LinkIcon className="size-3.5" />
              From URL
            </TabsTrigger>
          </TabsList>

          {/* ── PDF tab ── */}
          <TabsContent value="pdf" className="mt-4 space-y-3">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f);
                if (f && !pdfTitle)
                  setPdfTitle(f.name.replace(/\.pdf$/i, ""));
              }}
            />
            <Button
              type="button"
              variant="outline"
              className="w-full truncate"
              onClick={() => fileRef.current?.click()}
            >
              {file ? file.name : "Choose PDF file…"}
            </Button>
            <Input
              placeholder="Paper title"
              value={pdfTitle}
              onChange={(e) => setPdfTitle(e.target.value)}
            />
            {error && (
              <p className="text-xs text-destructive">{error}</p>
            )}
            <div className="flex gap-2">
              <Button
                className="flex-1"
                onClick={handlePdfSubmit}
                disabled={!file || !pdfTitle.trim() || submitting}
              >
                {submitting ? "Uploading…" : "Upload & Index"}
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setOpen(false);
                  reset();
                }}
              >
                Cancel
              </Button>
            </div>
          </TabsContent>

          {/* ── URL tab ── */}
          <TabsContent value="url" className="mt-4 space-y-3">
            <Input
              placeholder="Paper title"
              value={urlTitle}
              onChange={(e) => setUrlTitle(e.target.value)}
            />
            <Input
              placeholder="https://ieeexplore.ieee.org/…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            {paywalled && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
                <p className="font-medium text-destructive">
                  Paywalled — no open-access version found.
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  The paper was saved. Upload the PDF to index it.
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-2"
                  onClick={() => {
                    setTab("pdf");
                    setPdfTitle(urlTitle);
                    setPaywalled(false);
                  }}
                >
                  Upload PDF instead
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="mt-1"
                  onClick={() => { setOpen(false); reset(); }}
                >
                  Cancel
                </Button>
              </div>
            )}
            {error && (
              <p className="text-xs text-destructive">{error}</p>
            )}
            {!paywalled && (
              <div className="flex gap-2">
                <Button
                  className="flex-1"
                  onClick={handleUrlSubmit}
                  disabled={!url.trim() || !urlTitle.trim() || submitting}
                >
                  {submitting ? "Fetching…" : "Fetch & Index"}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setOpen(false);
                    reset();
                  }}
                >
                  Cancel
                </Button>
              </div>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
