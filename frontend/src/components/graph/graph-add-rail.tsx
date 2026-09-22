"use client";

import type { RefObject } from "react";
import type { GraphPaper } from "@/lib/graph-data";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * The right-hand rail. "Add a paper" toggles each placeable paper on and off
 * the canvas, and is the keyboard path for both: nothing on this screen is
 * reachable only by dragging. "Not available" lists the papers that cannot be
 * placed, each with its reason under the title and again on a tooltip over its
 * disabled button.
 */
export function GraphAddRail({
  availablePapers,
  onCanvas,
  onToggle,
  unavailable,
  rowRefs,
}: {
  availablePapers: GraphPaper[];
  onCanvas: Set<string>;
  onToggle: (paperId: string) => void;
  unavailable: { paper: GraphPaper; reason: string }[];
  rowRefs: RefObject<Record<string, HTMLButtonElement | null>>;
}) {
  return (
    <aside className="slide-in-rail w-full shrink-0 space-y-5 lg:w-72">
      <div>
        <h2 className="text-[13px] font-semibold">Add a paper</h2>
        <ul className="mt-2 space-y-1.5">
          {availablePapers.map((paper) => {
            const isOn = onCanvas.has(paper.id);
            return (
              <li key={paper.id} className="flex items-center justify-between gap-2">
                <span className="min-w-0 flex-1 truncate text-[13px]">{paper.title}</span>
                <Button
                  ref={(el) => {
                    rowRefs.current[paper.id] = el;
                  }}
                  size="sm"
                  variant={isOn ? "secondary" : "outline"}
                  aria-pressed={isOn}
                  onClick={() => onToggle(paper.id)}
                >
                  {isOn ? "On canvas" : "Add"}
                </Button>
              </li>
            );
          })}
        </ul>
      </div>

      <div>
        <h3 className="text-[13px] font-semibold text-muted-foreground">Not available</h3>
        <ul className="mt-2 space-y-2.5">
          {unavailable.map(({ paper, reason }) => (
            <li key={paper.id} className="flex items-center justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] text-muted-foreground">{paper.title}</p>
                <p className="text-[11px] text-muted-foreground/80">{reason}</p>
              </div>
              <Tooltip>
                <TooltipTrigger render={<span tabIndex={0} />}>
                  <Button size="sm" variant="outline" disabled>
                    Add
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{reason}</TooltipContent>
              </Tooltip>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
