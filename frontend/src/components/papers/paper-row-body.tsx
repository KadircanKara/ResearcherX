"use client";

import { ExternalLink } from "lucide-react";
import { formatDate } from "@/lib/format";
import { abstractExcerpt, sourceLine, stateDetail, type PaperState } from "@/lib/papers";
import type { Paper } from "@/lib/types";

/**
 * The body shown under an expanded paper row: the retriever's answer for
 * this paper, in words, and the handful of things you can do about it.
 *
 * The probe that produced `state` is the page's — one request for chunk 0,
 * issued when the row is opened, never a sweep over the library.
 */
export function PaperRowBody({
  paper,
  state,
  downloading,
  deleting,
  onCheckAgain,
  onRename,
  onRemove,
  onDownload,
}: {
  paper: Paper;
  state: PaperState;
  downloading: boolean;
  deleting: boolean;
  onCheckAgain: () => void;
  onRename: () => void;
  onRemove: () => void;
  onDownload: () => void;
}) {
  const checking = state.kind === "checking";
  const link = paper.resolved_pdf_url ?? paper.pdf_url;
  const abstract = paper.abstract?.trim();

  return (
    <div className="fade-block space-y-3 border-t px-4 py-4 text-[13px]">
      <div>
        <p className="font-semibold text-foreground">{state.label}</p>
        <p className="mt-0.5 font-mono text-[12px] text-muted-foreground">
          {sourceLine(paper)} · added {formatDate(paper.created_at)}
        </p>
      </div>
      <p className="text-muted-foreground">{stateDetail(state)}</p>
      {abstract && (
        <blockquote className="border-l-2 border-border pl-3 italic text-muted-foreground">
          {abstractExcerpt(abstract)}
        </blockquote>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-2 pt-1">
        <button
          type="button"
          className="text-primary underline-offset-2 hover:underline disabled:pointer-events-none disabled:opacity-50"
          disabled={checking}
          onClick={onCheckAgain}
        >
          {checking ? "Checking…" : "Check the retriever again"}
        </button>
        {/* A paper we hold the PDF for downloads; a link-sourced one opens
            where it lives; a paper with neither was added before PDFs were
            kept, and gets no control at all rather than a dead one. */}
        {paper.has_pdf ? (
          <button
            type="button"
            className="text-primary underline-offset-2 hover:underline disabled:pointer-events-none disabled:opacity-50"
            disabled={downloading}
            onClick={onDownload}
          >
            {downloading ? "Downloading…" : "Download PDF"}
          </button>
        ) : link ? (
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
          >
            Open the paper&apos;s link
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        ) : null}
        <button
          type="button"
          className="text-primary underline-offset-2 hover:underline"
          onClick={onRename}
        >
          Rename
        </button>
        <button
          type="button"
          className="text-destructive underline-offset-2 hover:underline disabled:pointer-events-none disabled:opacity-50"
          disabled={deleting}
          onClick={onRemove}
        >
          {deleting ? "Removing…" : "Remove from library"}
        </button>
      </div>
    </div>
  );
}
