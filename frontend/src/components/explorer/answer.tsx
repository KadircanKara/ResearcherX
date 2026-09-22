"use client";

import { useState } from "react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { ExplorationTurn, Source } from "@/lib/explorer-data";

/**
 * A numbered citation marker.
 *
 * It is a real `<button>`, and it opens on FOCUS as well as hover: a hover-only
 * source card is a card a keyboard never reaches. Pointer leaving the chip or
 * the card closes it, so the pointer can travel from one to the other.
 */
function Citation({ source }: { source: Source }) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={`Source ${source.id}: ${source.title}`}
            onMouseEnter={() => setOpen(true)}
            onMouseLeave={() => setOpen(false)}
            onFocus={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded bg-primary/10 px-1 font-mono text-[11px] font-semibold text-primary focus:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
        }
      >
        {source.id}
      </PopoverTrigger>
      <PopoverContent
        // The card is not focus-trapped and does not steal focus: the chip
        // keeps it, so Tab moves on to the next chip rather than into a card
        // the reader never asked to enter.
        initialFocus={false}
        finalFocus={false}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        <p className="text-xs font-semibold">{source.title}</p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {source.domain} · {source.year}
        </p>
        <p className="mt-2 text-xs leading-relaxed">{source.summary}</p>
      </PopoverContent>
    </Popover>
  );
}

/**
 * The answer, one citation per sentence, followed by the sources it drew on.
 *
 * `streamingText` is the replay's partial answer. While it is set the source
 * row is withheld: the row is what the finished turn cites, and showing it
 * beside a half-written answer would claim sources the text has not reached.
 */
export function Answer({
  turn,
  streamingText,
}: {
  turn: ExplorationTurn;
  streamingText?: string | undefined;
}) {
  const shown = streamingText ?? turn.answer;
  const sentences = shown.match(/[^.!?]+[.!?]?/g) ?? [shown];

  return (
    <div aria-live="polite">
      <div className="space-y-3 text-sm leading-7">
        {sentences.map((sentence, index) => {
          const citedSource = turn.sources[index];
          return (
            <span key={`${sentence}-${index}`}>
              {sentence.trim()}{" "}
              {citedSource ? <Citation source={citedSource} /> : null}{" "}
            </span>
          );
        })}
      </div>
      {streamingText === undefined ? (
        <div className="mt-4 flex gap-2 overflow-x-auto pb-2">
          {turn.sources.map((item) => (
            <div
              key={item.id}
              className="min-w-[13rem] rounded-lg border bg-card p-3"
            >
              <div className="flex gap-2">
                <span
                  aria-hidden="true"
                  className="grid size-7 shrink-0 place-items-center rounded bg-secondary text-xs font-semibold"
                >
                  {item.domain.charAt(0).toUpperCase()}
                </span>
                <div className="min-w-0">
                  <p className="line-clamp-2 text-xs font-medium">{item.title}</p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    {item.domain} · {item.year}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
