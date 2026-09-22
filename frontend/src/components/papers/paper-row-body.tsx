"use client";

import { ExternalLink } from "lucide-react";
import { formatAdded, sourceLine, stateDetail, type PaperState } from "@/lib/papers";
import type { Paper } from "@/lib/types";
import { cn } from "@/lib/utils";

const ABSTRACT_MAX = 320;

const ACTION =
  "text-primary underline-offset-2 transition-colors hover:underline disabled:pointer-events-none disabled:opacity-50";

/**
 * What an opened row shows: the retriever's answer for this paper, in
 * words, and the handful of things you can do about it.
 *
 * The probe that produced `state` is the caller's — one request for chunk 0,
 * issued when the row is opened, never a sweep over the library.
 */
export function PaperRowBody({
  paper,
  state,
  canEdit,
  checking,
  downloading,
  deleting,
  onCheckAgain,
  onRename,
  onRemove,
  onDownload,
}: {
  paper: Paper;
  state: PaperState;
  canEdit: boolean;
  checking: boolean;
  downloading: boolean;
  deleting: boolean;
  onCheckAgain: () => void;
  onRename: () => void;
  onRemove: () => void;
  onDownload: () => void;
}) {
  const link = paper.resolved_pdf_url ?? paper.pdf_url;
  const abstract = paper.abstract?.trim();

  return (
    <div
      // A confirmed-empty paper is the one state worth colouring the whole
      // body: it is the only one the reader has to act on.
      className={cn(
        "fade-block space-y-3 border-t px-4 py-4 text-[13px]",
        state.tone === "bad" && "bg-destructive/5"
      )}
    >
      <div>
        <p className="font-medium text-foreground">{state.label}</p>
        <p className="mt-0.5 font-mono text-[12px] text-muted-foreground">
          {sourceLine(paper)} · added {formatAdded(paper.created_at)}
        </p>
      </div>

      <p className="text-muted-foreground">{stateDetail(state)}</p>

      {abstract && (
        <blockquote className="border-l-2 border-border pl-3 text-muted-foreground italic">
          {abstract.slice(0, ABSTRACT_MAX)}
          {abstract.length > ABSTRACT_MAX ? "…" : ""}
        </blockquote>
      )}

      <div className="flex flex-wrap gap-x-4 gap-y-2 pt-1">
        <button type="button" className={ACTION} disabled={checking} onClick={onCheckAgain}>
          {checking ? "Checking…" : "Check the retriever again"}
        </button>

        {/* A paper we hold the PDF for downloads; a link-sourced one opens
            where it lives; a paper with neither was added before PDFs were
            kept, and gets no control at all rather than a dead one. */}
        {paper.has_pdf ? (
          <button type="button" className={ACTION} disabled={downloading} onClick={onDownload}>
            {downloading ? "Downloading…" : "Download PDF"}
          </button>
        ) : link ? (
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className={`inline-flex items-center gap-1 ${ACTION}`}
          >
            Open the paper&apos;s link
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        ) : null}

        {canEdit && (
          <button type="button" className={ACTION} onClick={onRename}>
            Rename
          </button>
        )}
        {canEdit && (
          <button
            type="button"
            className="text-destructive underline-offset-2 transition-colors hover:underline disabled:pointer-events-none disabled:opacity-50"
            disabled={deleting}
            onClick={onRemove}
          >
            {deleting ? "Removing…" : "Remove from library"}
          </button>
        )}
      </div>
    </div>
  );
}
