"use client";

import { Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Candidate } from "@/lib/explorer-data";
import {
  distanceLabel,
  distanceMeterPercent,
  formatDistance,
} from "@/lib/explorer";

/**
 * One paper Explorer found outside the library, with the case for adding it:
 * how far it sits from what the project already holds, the evidence, and which
 * of the question's terms it matched and missed.
 *
 * Three action states, and they are not interchangeable:
 *   - `library` — already held; there is no action, only a statement.
 *   - `against` — Explorer argues against it, so the button is quiet and reads
 *     "Add anyway". The judgement is the assistant's; the decision is not.
 *   - otherwise — the ordinary "Add to library".
 */
export function CandidateCard({
  paper,
  added,
  onAdd,
}: {
  paper: Candidate;
  added: boolean;
  onAdd: () => void;
}) {
  const label = distanceLabel(paper.distance);

  return (
    <article className="fade-block rounded-lg border bg-card p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:justify-between">
        <div className="min-w-0">
          <h4 className="font-semibold leading-snug">{paper.title}</h4>
          <p className="mt-1 text-xs text-muted-foreground">{paper.byline}</p>
        </div>
        <div className="w-full shrink-0 sm:w-32">
          <div className="font-mono text-[11px]">
            {formatDistance(paper.distance)}
          </div>
          {/* The meter is decoration over the number and the label beside it,
              so it carries no ARIA role of its own. */}
          <div
            aria-hidden="true"
            className="mt-1 h-1 overflow-hidden rounded-full bg-secondary"
          >
            <div
              className="h-full bg-primary"
              style={{ width: `${distanceMeterPercent(paper.distance)}%` }}
            />
          </div>
          <p className="mt-1 text-[10px] text-muted-foreground">{label}</p>
        </div>
      </div>
      <p className="mt-3 text-[13px] leading-relaxed">{paper.evidence}</p>
      <div className="mt-3 flex flex-wrap gap-1">
        {paper.matched.map((term) => (
          <Badge key={term} variant="secondary" className="font-normal">
            {term}
          </Badge>
        ))}
        {paper.missed.map((term) => (
          <Badge
            key={term}
            variant="outline"
            className="font-normal text-muted-foreground"
          >
            {term}
          </Badge>
        ))}
      </div>
      <div className="mt-4 flex justify-end">
        {paper.status === "library" ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <Check aria-hidden="true" className="size-3.5" />
            In library
          </span>
        ) : (
          <Button
            variant={paper.status === "against" ? "ghost" : "default"}
            size="sm"
            className="rounded-full"
            onClick={onAdd}
            disabled={added}
          >
            {added ? (
              <>
                <Check aria-hidden="true" />
                Added
              </>
            ) : paper.status === "against" ? (
              "Add anyway"
            ) : (
              "Add to library"
            )}
          </Button>
        )}
      </div>
    </article>
  );
}
