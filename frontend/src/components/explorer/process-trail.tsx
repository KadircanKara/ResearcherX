"use client";

import { useState } from "react";
import { Check, ChevronDown, Circle, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import type { ExplorationTurn, SearchQuery } from "@/lib/explorer-data";
import {
  REPLAY_STAGE_DONE,
  replayLabel,
  reviewLabel,
  severityClass,
  trailSummary,
} from "@/lib/explorer";
import { cn } from "@/lib/utils";

/**
 * One search inside the trail: the query, how many sources it returned, how the
 * validator judged it, and — once expanded — the sources themselves.
 *
 * `complete` dims a step a replay has not reached yet. It is opacity rather
 * than absence so the trail does not reflow as the run progresses.
 */
function SearchStep({ query, complete }: { query: SearchQuery; complete: boolean }) {
  const [open, setOpen] = useState(false);

  return (
    <div className={cn("relative flex gap-3", !complete && "opacity-45")}>
      <Circle
        aria-hidden="true"
        className={cn(
          "z-10 mt-0.5 size-4 shrink-0 fill-background text-border",
          complete && "fill-primary text-primary"
        )}
      />
      <Collapsible open={open} onOpenChange={setOpen} className="min-w-0 flex-1">
        <CollapsibleTrigger
          render={
            <Button
              variant="ghost"
              className="h-auto w-full justify-between gap-3 rounded-md px-0 py-0 text-left text-xs font-normal hover:bg-transparent"
            />
          }
        >
          <span className="min-w-0">
            <span className="font-medium">Searched:</span>{" "}
            <code className="font-mono text-[11px]">{query.query}</code>
          </span>
          <ChevronDown
            aria-hidden="true"
            className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-180")}
          />
        </CollapsibleTrigger>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground">
            {query.sources.length} sources
          </span>
          <Badge
            variant="outline"
            className={cn(
              "h-5 px-1.5 text-[10px] font-medium",
              query.status === "retried once" && "border-warning text-warning",
              query.status === "kept, degraded" && "text-muted-foreground"
            )}
          >
            {query.status}
          </Badge>
        </div>
        {query.revisedQuery ? (
          <p className="mt-1 font-mono text-[11px] text-warning">
            → {query.revisedQuery}
          </p>
        ) : null}
        <CollapsibleContent className="pt-2">
          <ul className="space-y-2 border-l pl-3">
            {query.sources.map((source) => (
              <li key={`${query.query}-${source.id}`}>
                <p className="font-medium leading-snug">{source.title}</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">
                  {source.domain}
                </p>
                <p className="mt-0.5 leading-relaxed text-muted-foreground">
                  {source.summary}
                </p>
              </li>
            ))}
          </ul>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

/**
 * How the answer was reached: what was planned, what was searched, what came
 * back, and what the review found. Collapsed by default — the answer is the
 * point, and this is the audit trail behind it.
 *
 * `replayStage` is `null` when the turn is simply history. During a replay it
 * counts up through `REPLAY_STAGE_DONE`, and the collapsed label narrates the
 * stage instead of summarising the finished run.
 */
export function ProcessTrail({
  turn,
  replayStage = null,
}: {
  turn: ExplorationTurn;
  replayStage?: number | null;
}) {
  const [open, setOpen] = useState(false);
  const running = replayStage !== null && replayStage < REPLAY_STAGE_DONE;
  const label =
    (replayStage === null ? null : replayLabel(replayStage, turn.queries.length)) ??
    trailSummary(turn);

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border bg-card">
      <CollapsibleTrigger
        render={
          <Button
            variant="ghost"
            className="h-auto w-full justify-between rounded-lg px-3 py-2.5 text-left text-xs font-normal"
          />
        }
      >
        <span className="flex min-w-0 items-center gap-2">
          <Sparkles
            aria-hidden="true"
            className={cn(
              "size-3.5 shrink-0 text-primary",
              // motion-safe: the pulse is decoration, and a reader who asked
              // for less motion still gets the label, which carries the state.
              running && "motion-safe:animate-pulse"
            )}
          />
          <span className="truncate">{label}</span>
        </span>
        <ChevronDown
          aria-hidden="true"
          className={cn("size-4 shrink-0 transition-transform", open && "rotate-180")}
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t px-4 py-3 text-xs">
        {/* The spine behind the step markers. A pseudo-element, not a real
            node, so it cannot take a tab stop or be read out. */}
        <div className="relative space-y-4 before:absolute before:bottom-2 before:left-[7px] before:top-2 before:w-px before:bg-border">
          <div className="relative flex gap-3">
            <Check aria-hidden="true" className="z-10 size-4 shrink-0 rounded-full bg-background text-primary" />
            <div>
              <p className="font-medium">Planned {turn.queries.length} queries</p>
              <p className="mt-1 text-muted-foreground">{turn.rationale}</p>
            </div>
          </div>

          {turn.queries.map((query, index) => (
            <SearchStep
              key={query.query}
              query={query}
              complete={replayStage === null || replayStage > index + 1}
            />
          ))}

          <div className="relative flex gap-3">
            <Check aria-hidden="true" className="z-10 size-4 shrink-0 rounded-full bg-background text-primary" />
            <p className="font-medium">Wrote the answer</p>
          </div>

          <div className="relative flex gap-3">
            <Check aria-hidden="true" className="z-10 size-4 shrink-0 rounded-full bg-background text-primary" />
            <div>
              <p className="font-medium">Reviewed: {reviewLabel(turn.review)}</p>
              {turn.review !== "pass" ? (
                <div className="mt-2 space-y-3">
                  {turn.review.map((issue) => (
                    <div
                      key={`${issue.claim}-${issue.severity}`}
                      className="grid grid-cols-[auto_1fr] gap-x-2"
                    >
                      <span aria-hidden="true" className={cn("mt-0.5", severityClass(issue.severity))}>
                        ●
                      </span>
                      <div>
                        <p className="font-medium text-foreground">
                          <span className="capitalize">{issue.severity}</span>:{" "}
                          {issue.claim}
                        </p>
                        <p className="mt-0.5 leading-relaxed text-muted-foreground">
                          {issue.note}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
