"use client";

import { useRef, useState } from "react";
import { FileText, Link as LinkIcon, Upload } from "lucide-react";
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

function CharCounter({ value, max, warn }: { value: string; max: number; warn: number }) {
  if (value.length <= warn) return null;
  return (
    <p className="text-right text-xs text-muted-foreground">{max - value.length} chars left</p>
  );
}

function BodyTextarea({
  value,
  onChange,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder ?? "Paper body text…"}
      disabled={disabled}
      rows={6}
      className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
    />
  );
}

function AbstractTextarea({
  value,
  onChange,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder ?? "Abstract…"}
      disabled={disabled}
      rows={3}
      className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
    />
  );
}

export function AddPaperDialog({ projectId, onAdded, children }: AddPaperDialogProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<string>("pdf");

  // PDF tab
  const [file, setFile] = useState<File | null>(null);
  const [pdfTitle, setPdfTitle] = useState("");
  const [pdfAbstract, setPdfAbstract] = useState("");
  const [pdfBody, setPdfBody] = useState("");
  const [extractingPdf, setExtractingPdf] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // URL tab
  const [url, setUrl] = useState("");
  const [urlTitle, setUrlTitle] = useState("");
  const [urlAbstract, setUrlAbstract] = useState("");
  const [urlTitleMode, setUrlTitleMode] = useState<
    "idle" | "extracting" | "extracted" | "requires_manual"
  >("idle");

  // Text tab
  const [textTitle, setTextTitle] = useState("");
  const [textAbstract, setTextAbstract] = useState("");
  const [textBody, setTextBody] = useState("");

  // Shared
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paywalled, setPaywalled] = useState(false);

  function reset() {
    setFile(null);
    setPdfTitle("");
    setPdfAbstract("");
    setPdfBody("");
    setExtractingPdf(false);
    setUrl("");
    setUrlTitle("");
    setUrlAbstract("");
    setUrlTitleMode("idle");
    setTextTitle("");
    setTextAbstract("");
    setTextBody("");
    setError(null);
    setPaywalled(false);
    setTab("pdf");
    if (fileRef.current) fileRef.current.value = "";
  }

  async function extractPdfMeta(f: File) {
    setExtractingPdf(true);
    try {
      const bytes = await f.arrayBuffer();
      const { title, abstract, body } = await suggestTitle(projectId, bytes);
      if (title) setPdfTitle(title.slice(0, TITLE_MAX));
      if (abstract) setPdfAbstract(abstract);
      if (body) setPdfBody(body);
    } catch {
      // fail-open: title stays as filename
    } finally {
      setExtractingPdf(false);
    }
  }

  async function extractUrlMeta(urlValue: string) {
    if (!urlValue.trim()) return;
    setUrlTitleMode("extracting");
    setUrlTitle("");
    setUrlAbstract("");
    try {
      const { title, abstract, requires_manual } = await suggestTitleFromUrl(
        projectId,
        urlValue.trim()
      );
      if (requires_manual || !title) {
        setUrlTitleMode("requires_manual");
      } else {
        setUrlTitle(title.slice(0, TITLE_MAX));
        if (abstract) setUrlAbstract(abstract);
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
      const bytes = await file.arrayBuffer();
      const paper = await createPaper(projectId, {
        title: pdfTitle.trim(),
        abstract: pdfAbstract.trim() || null,
        body: pdfBody.trim() || null,
      });
      try {
        await ingestPaper(projectId, paper.id, bytes);
        setOpen(false);
        reset();
        onAdded();
      } catch {
        await deletePaper(projectId, paper.id).catch(() => {});
        setError("Upload failed. Please try again.");
      }
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
        abstract: urlAbstract.trim() || null,
        pdf_url: url.trim(),
      });
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
    } catch {
      setError("Failed to save paper. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTextSubmit() {
    if (!textTitle.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await createPaper(projectId, {
        title: textTitle.trim(),
        abstract: textAbstract.trim() || null,
        body: textBody.trim() || null,
      });
      setOpen(false);
      reset();
      onAdded();
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

        <Tabs
          value={tab}
          onValueChange={(v) => {
            if (v) setTab(v);
            setError(null);
            setPaywalled(false);
          }}
          className="flex min-h-0 flex-1 flex-col"
        >
          <TabsList className="w-full shrink-0">
            <TabsTrigger value="pdf" className="flex-1 gap-1.5">
              <Upload className="size-3.5" />
              Upload PDF
            </TabsTrigger>
            <TabsTrigger value="url" className="flex-1 gap-1.5">
              <LinkIcon className="size-3.5" />
              From URL
            </TabsTrigger>
            <TabsTrigger value="text" className="flex-1 gap-1.5">
              <FileText className="size-3.5" />
              Text
            </TabsTrigger>
          </TabsList>

          {/* ── PDF tab ── */}
          <TabsContent value="pdf" className="mt-4 min-h-0 flex-1 overflow-y-auto">
            <div className="space-y-3 pb-2">
              <input
                ref={fileRef}
                type="file"
                accept=".pdf"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  setFile(f);
                  setPdfAbstract("");
                  setPdfBody("");
                  if (f) {
                    const fallback = f.name.replace(/\.pdf$/i, "").slice(0, TITLE_MAX);
                    setPdfTitle(fallback);
                    extractPdfMeta(f);
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
                <FieldLabel label="Title" />
                <Input
                  placeholder={extractingPdf ? "Extracting…" : "Paper title"}
                  value={pdfTitle}
                  maxLength={TITLE_MAX}
                  disabled={extractingPdf}
                  onChange={(e) => setPdfTitle(e.target.value)}
                />
                <CharCounter value={pdfTitle} max={TITLE_MAX} warn={TITLE_WARN} />
              </div>

              <div className="space-y-1">
                <FieldLabel label="Abstract" />
                <AbstractTextarea
                  value={pdfAbstract}
                  onChange={setPdfAbstract}
                  placeholder={extractingPdf ? "Extracting…" : "Abstract…"}
                  disabled={extractingPdf}
                />
              </div>

              <div className="space-y-1">
                <FieldLabel label="Body" />
                <BodyTextarea
                  value={pdfBody}
                  onChange={setPdfBody}
                  placeholder={extractingPdf ? "Extracting…" : "Paper body text…"}
                  disabled={extractingPdf}
                />
              </div>

              {error && <p className="text-xs text-destructive">{error}</p>}

              <div className="flex gap-2">
                <Button
                  className="flex-1"
                  onClick={handlePdfSubmit}
                  disabled={!file || !pdfTitle.trim() || submitting || extractingPdf}
                >
                  {submitting ? "Uploading…" : "Upload & Index"}
                </Button>
                <Button variant="ghost" onClick={() => { setOpen(false); reset(); }}>
                  Cancel
                </Button>
              </div>
            </div>
          </TabsContent>

          {/* ── URL tab ── */}
          <TabsContent value="url" className="mt-4 min-h-0 flex-1 overflow-y-auto">
            <div className="space-y-3 pb-2">
              <div className="space-y-1">
                <FieldLabel label="URL" />
                <Input
                  placeholder="https://arxiv.org/abs/…"
                  value={url}
                  onChange={(e) => {
                    setUrl(e.target.value);
                    setUrlTitleMode("idle");
                    setUrlTitle("");
                    setUrlAbstract("");
                  }}
                  onBlur={() => extractUrlMeta(url)}
                />
              </div>

              {urlTitleMode !== "idle" && (
                <>
                  <div className="space-y-1">
                    <FieldLabel label="Title" />
                    <Input
                      placeholder={
                        urlTitleMode === "extracting"
                          ? "Extracting…"
                          : urlTitleMode === "requires_manual"
                          ? "Enter title manually"
                          : "Paper title"
                      }
                      value={urlTitle}
                      maxLength={TITLE_MAX}
                      disabled={urlTitleMode === "extracting"}
                      onChange={(e) => setUrlTitle(e.target.value)}
                    />
                    {urlTitleMode === "requires_manual" && (
                      <p className="text-xs text-amber-600">
                        Couldn&apos;t extract title — enter it manually.
                      </p>
                    )}
                    <CharCounter value={urlTitle} max={TITLE_MAX} warn={TITLE_WARN} />
                  </div>

                  <div className="space-y-1">
                    <FieldLabel label="Abstract" />
                    <AbstractTextarea
                      value={urlAbstract}
                      onChange={setUrlAbstract}
                      placeholder={urlTitleMode === "extracting" ? "Extracting…" : "Abstract…"}
                      disabled={urlTitleMode === "extracting"}
                    />
                  </div>
                </>
              )}

              {paywalled && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
                  <p className="font-medium text-destructive">
                    Paywalled — no open-access version found.
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Upload the PDF directly to index it.
                  </p>
                  <div className="mt-2 flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setTab("pdf");
                        setPdfTitle(urlTitle);
                        setPdfAbstract(urlAbstract);
                        setPaywalled(false);
                      }}
                    >
                      Upload PDF instead
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => { setOpen(false); reset(); }}>
                      Cancel
                    </Button>
                  </div>
                </div>
              )}

              {error && <p className="text-xs text-destructive">{error}</p>}

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
                  <Button variant="ghost" onClick={() => { setOpen(false); reset(); }}>
                    Cancel
                  </Button>
                </div>
              )}
            </div>
          </TabsContent>

          {/* ── Text tab ── */}
          <TabsContent value="text" className="mt-4 min-h-0 flex-1 overflow-y-auto">
            <div className="space-y-3 pb-2">
              <div className="space-y-1">
                <FieldLabel label="Title" />
                <Input
                  placeholder="Paper title"
                  value={textTitle}
                  maxLength={TITLE_MAX}
                  onChange={(e) => setTextTitle(e.target.value)}
                />
                <CharCounter value={textTitle} max={TITLE_MAX} warn={TITLE_WARN} />
              </div>

              <div className="space-y-1">
                <FieldLabel label="Abstract" />
                <AbstractTextarea value={textAbstract} onChange={setTextAbstract} />
              </div>

              <div className="space-y-1">
                <FieldLabel label="Body" />
                <BodyTextarea value={textBody} onChange={setTextBody} />
              </div>

              {error && <p className="text-xs text-destructive">{error}</p>}

              <div className="flex gap-2">
                <Button
                  className="flex-1"
                  onClick={handleTextSubmit}
                  disabled={!textTitle.trim() || submitting}
                >
                  {submitting ? "Saving…" : "Save"}
                </Button>
                <Button variant="ghost" onClick={() => { setOpen(false); reset(); }}>
                  Cancel
                </Button>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
