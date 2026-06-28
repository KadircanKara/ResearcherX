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
  suggestTitle,
  suggestTitleFromUrl,
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
  const [extractingPdfTitle, setExtractingPdfTitle] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // URL tab
  const [url, setUrl] = useState("");
  const [urlTitle, setUrlTitle] = useState("");
  const [urlTitleMode, setUrlTitleMode] = useState<
    "idle" | "extracting" | "extracted" | "requires_manual"
  >("idle");

  // Shared
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paywalled, setPaywalled] = useState(false);

  function reset() {
    setFile(null);
    setPdfTitle("");
    setExtractingPdfTitle(false);
    setUrl("");
    setUrlTitle("");
    setUrlTitleMode("idle");
    setError(null);
    setPaywalled(false);
    setTab("pdf");
    if (fileRef.current) fileRef.current.value = "";
  }

  async function extractPdfTitle(f: File) {
    setExtractingPdfTitle(true);
    try {
      const bytes = await f.arrayBuffer();
      const { title } = await suggestTitle(projectId, bytes);
      if (title) setPdfTitle(title.slice(0, 150));
    } catch {
      // fail-open: title stays as filename
    } finally {
      setExtractingPdfTitle(false);
    }
  }

  async function extractUrlTitle(urlValue: string) {
    if (!urlValue.trim()) return;
    setUrlTitleMode("extracting");
    setUrlTitle("");
    try {
      const { title, requires_manual } = await suggestTitleFromUrl(projectId, urlValue.trim());
      if (requires_manual || !title) {
        setUrlTitleMode("requires_manual");
      } else {
        setUrlTitle(title.slice(0, 150));
        setUrlTitleMode("extracted");
      }
    } catch {
      setUrlTitleMode("requires_manual");
    }
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
    if (!url.trim() || !urlTitle.trim() || submitting || urlTitleMode === "extracting") return;
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
      <DialogContent className="sm:max-w-lg">
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
          className="flex-col"
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
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f);
                if (f) {
                  const fallback = f.name.replace(/\.pdf$/i, "").slice(0, 150);
                  setPdfTitle(fallback);
                  extractPdfTitle(f);
                }
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
            <div className="space-y-1">
              <Input
                placeholder={extractingPdfTitle ? "Extracting title…" : "Paper title"}
                value={pdfTitle}
                maxLength={150}
                disabled={extractingPdfTitle}
                onChange={(e) => setPdfTitle(e.target.value)}
              />
              {pdfTitle.length > 120 && (
                <p className="text-right text-xs text-muted-foreground">
                  {150 - pdfTitle.length} chars left
                </p>
              )}
            </div>
            {error && (
              <p className="text-xs text-destructive">{error}</p>
            )}
            <div className="flex gap-2">
              <Button
                className="flex-1"
                onClick={handlePdfSubmit}
                disabled={!file || !pdfTitle.trim() || submitting || extractingPdfTitle}
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
              placeholder="https://ieeexplore.ieee.org/…"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setUrlTitleMode("idle");
                setUrlTitle("");
              }}
              onBlur={() => extractUrlTitle(url)}
            />
            {urlTitleMode !== "idle" && (
              <div className="space-y-1">
                <Input
                  placeholder={
                    urlTitleMode === "extracting"
                      ? "Extracting title…"
                      : urlTitleMode === "requires_manual"
                      ? "Enter title manually"
                      : "Paper title"
                  }
                  value={urlTitle}
                  maxLength={150}
                  disabled={urlTitleMode === "extracting"}
                  onChange={(e) => setUrlTitle(e.target.value)}
                />
                {urlTitleMode === "requires_manual" && (
                  <p className="text-xs text-amber-600">
                    Couldn&apos;t extract title — enter it manually.
                  </p>
                )}
                {urlTitle.length > 120 && (
                  <p className="text-right text-xs text-muted-foreground">
                    {150 - urlTitle.length} chars left
                  </p>
                )}
              </div>
            )}
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
                  disabled={
                    !url.trim() ||
                    !urlTitle.trim() ||
                    submitting ||
                    urlTitleMode === "extracting"
                  }
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
