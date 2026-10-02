"use client";

import { useEffect, useState } from "react";
import { Download, FileImage, FileQuestion, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { readBinaryFile, LatexRequestError } from "@/lib/latex";
import { basename, formatBytes, isImagePath } from "@/lib/latex-tree";

interface BinaryPreviewProps {
  projectId: string;
  documentId: string;
  path: string;
  sizeBytes: number;
}

/**
 * The one component in this set allowed to fetch: binary bytes are large and
 * needed only when a tab actually looks at them. Putting this in
 * `use-latex-document.ts` would fetch every image in the project the moment
 * its document was opened.
 */
export function BinaryPreview({ projectId, documentId, path, sizeBytes }: BinaryPreviewProps) {
  const isImage = isImagePath(path);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!isImage) return;
    let cancelled = false;
    let url: string | null = null;
    setImgUrl(null);
    setError(null);
    readBinaryFile(projectId, documentId, path)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setImgUrl(url);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof LatexRequestError ? err.userMessage : "Could not load the preview.");
      });
    return () => {
      cancelled = true;
      // Revoked on cleanup AND on every path change (this effect reruns
      // whenever `path` changes, tearing down the previous run first) -- a
      // blob URL left un-revoked leaks one per image tab ever opened.
      if (url) URL.revokeObjectURL(url);
    };
  }, [isImage, projectId, documentId, path]);

  async function download() {
    setDownloading(true);
    setError(null);
    try {
      const blob = await readBinaryFile(projectId, documentId, path);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = basename(path);
      a.click();
      // Deferred rather than revoked immediately after `click()`: Safari can
      // cancel a download that's still being handed off to the OS if the
      // blob: URL it points at is revoked out from under it synchronously.
      // A macrotask delay lets the click's own download handling complete
      // first; this is the standard workaround for that race.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (err) {
      setError(err instanceof LatexRequestError ? err.userMessage : "Could not download the file.");
    } finally {
      setDownloading(false);
    }
  }

  // The prototype's `LatexBinaryPreview` frame: icon (or, here, the real
  // image), the path in mono, then a muted line with the size.
  if (isImage) {
    return (
      <div className="flex h-full flex-1 flex-col items-center justify-center gap-2 overflow-auto bg-muted/20 p-4 text-center">
        {error ? (
          <FileImage className="size-10 text-muted-foreground" aria-hidden />
        ) : imgUrl ? (
          // next/image needs a static/whitelisted loader; this is a blob:
          // URL for arbitrary project-uploaded bytes fetched behind an auth
          // header, which next/image cannot optimize anyway.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={imgUrl} alt={basename(path)} className="max-h-[60%] max-w-full rounded-sm border bg-card shadow-sm" />
        ) : (
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
        )}
        <p className="font-mono text-[13px]">{path}</p>
        <p className="text-[12px] text-muted-foreground">
          {error ? `${error} ` : ""}
          {formatBytes(sizeBytes)}
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-1 flex-col items-center justify-center gap-2 bg-muted/20 p-4 text-center">
      <FileQuestion className="size-10 text-muted-foreground" aria-hidden />
      <p className="font-mono text-[13px]">{path}</p>
      <p className="text-[12px] text-muted-foreground">
        This file can&apos;t be edited here. {formatBytes(sizeBytes)}
      </p>
      <Button variant="outline" size="sm" className="mt-1 gap-1.5" onClick={() => void download()} disabled={downloading}>
        {downloading ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <Download className="size-3.5" aria-hidden />
        )}
        Download
      </Button>
      {error && <p className="text-[12px] text-destructive">{error}</p>}
    </div>
  );
}
