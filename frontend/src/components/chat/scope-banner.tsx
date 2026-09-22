import { Fragment } from "react";
import type { ScopeSegment } from "@/lib/chat-scope";
import { cn } from "@/lib/utils";

/**
 * What the retrieval was narrowed to, drawn as the app prototype's line.
 *
 * Deliberately NOT folded into the status line, which is about the phase: on
 * a RESOLVED scope the user clicked nothing, so this is the only place they
 * learn the answer was written from part of the library, and it has to
 * outlive the status line — into streaming, and under the finished answer —
 * to be read at all.
 *
 * Every word comes from `lib/chat-scope.ts`, which reads only the backend's
 * own `retrieving` event. This component adds no wording of its own; it
 * emphasises the segments that module marked as the user's own phrases.
 */
export function ScopeBanner({
  segments,
  note,
  className,
}: {
  segments: ScopeSegment[];
  /** A paper the user NAMED that returned nothing at all. */
  note?: string | null;
  className?: string;
}) {
  return (
    <div className={cn("space-y-0.5 text-xs text-muted-foreground", className)}>
      <p>
        {segments.map((segment, i) =>
          segment.emphasis ? (
            <span key={i} className="font-medium text-foreground">
              {segment.text}
            </span>
          ) : (
            <Fragment key={i}>{segment.text}</Fragment>
          )
        )}
      </p>
      {note ? <p>{note}</p> : null}
    </div>
  );
}
