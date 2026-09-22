"use client";

import { Check } from "lucide-react";
import {
  MOCK_NOW,
  libraryPapers,
  type Candidate,
  type ExplorationThread,
} from "@/lib/explorer-data";
import { formatActivity, formatStarted } from "@/lib/explorer";

/**
 * The exploration's context: what it scored against, what it has added, and
 * what it cost. Rendered twice — in the right rail above 1280px and inside the
 * Details sheet below it — from one component, so the two cannot drift.
 */
export function Rail({
  thread,
  added,
  candidates,
}: {
  thread: ExplorationThread;
  added: string[];
  candidates: Candidate[];
}) {
  const totalChunks = libraryPapers.reduce((total, paper) => total + paper.chunks, 0);

  return (
    <div className="space-y-3">
      <div className="rounded-lg border bg-card p-4">
        <h3 className="text-xs font-semibold">Scored against</h3>
        <div className="mt-3 space-y-2">
          {libraryPapers.map((paper) => (
            <div
              key={paper.title}
              className="flex items-start justify-between gap-3 text-xs"
            >
              <span>{paper.title}</span>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                {paper.chunks} chunks
              </span>
            </div>
          ))}
        </div>
        <div className="mt-3 border-t pt-3 text-xs font-medium">
          {totalChunks} chunks total
        </div>
      </div>

      <div className="rounded-lg border bg-card p-4">
        <h3 className="text-xs font-semibold">Added this session</h3>
        {/* Announced, because adding happens far down the page and the rail
            may be off screen — or behind the Details sheet — when it does. */}
        <div className="mt-3 space-y-3" aria-live="polite">
          <div className="flex gap-2 text-xs">
            <Check aria-hidden="true" className="size-4 shrink-0 text-primary" />
            <span>{thread.baselineAdded}</span>
          </div>
          {added.map((id) => {
            const paper = candidates.find((candidate) => candidate.id === id);
            return paper ? (
              <div key={id} className="slide-in-rail flex gap-2 text-xs">
                <Check aria-hidden="true" className="size-4 shrink-0 text-primary" />
                <span>{paper.title}</span>
              </div>
            ) : null;
          })}
        </div>
      </div>

      <div className="rounded-lg border bg-card p-4">
        <h3 className="text-xs font-semibold">This exploration</h3>
        <dl className="mt-3 grid grid-cols-2 gap-y-2 text-xs">
          <dt className="text-muted-foreground">Started</dt>
          <dd className="text-right">{formatStarted(thread.started, MOCK_NOW)}</dd>
          <dt className="text-muted-foreground">Last activity</dt>
          <dd className="text-right">{formatActivity(thread.activity, MOCK_NOW)}</dd>
          <dt className="text-muted-foreground">Exchanges</dt>
          <dd className="text-right font-mono">{thread.turns.length}</dd>
          <dt className="text-muted-foreground">Considered</dt>
          <dd className="text-right font-mono">{thread.considered}</dd>
          <dt className="text-muted-foreground">Added</dt>
          {/* The baseline paper plus whatever this session added. */}
          <dd className="text-right font-mono text-primary">{added.length + 1}</dd>
        </dl>
      </div>
    </div>
  );
}
