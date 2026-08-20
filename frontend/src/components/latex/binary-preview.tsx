"use client";

import { useEffect, useState } from "react";
import { Download, File as FileIcon, Loader2 } from "lucide-react";
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

  if (isImage) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 overflow-auto p-4">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!error && !imgUrl && <Loader2 className="size-5 animate-spin text-muted-foreground" />}
        {imgUrl && (
          // next/image needs a static/whitelisted loader; this is a blob:
          // URL for arbitrary project-uploaded bytes fetched behind an auth
          // header, which next/image cannot optimize anyway.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={imgUrl} alt={basename(path)} className="max-w-full rounded-md border border-border" />
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-4">
      <div className="flex flex-col items-center gap-2 rounded-lg border border-border px-6 py-8">
        <FileIcon className="size-8 text-muted-foreground" />
        <span className="font-medium text-foreground">{basename(path)}</span>
        <span className="text-xs text-muted-foreground">{formatBytes(sizeBytes)}</span>
        <Button size="sm" variant="outline" onClick={() => void download()} disabled={downloading}>
          {downloading ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
          Download
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
