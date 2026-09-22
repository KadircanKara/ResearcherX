import { Search } from "lucide-react";
import type { ScopeSegment } from "@/lib/chat-scope";

/**
 * What the retrieval was narrowed to, for the turn in flight.
 *
 * Deliberately NOT folded into the status line above it, which is about the
 * phase: on a RESOLVED scope the user clicked nothing, so this is the only
 * place they learn the answer was written from part of the library, and it
 * has to outlive the status line — into streaming — to be read at all.
 *
 * Every word comes from `lib/chat-scope.ts`, which reads only the backend's
 * own `retrieving` event. This component adds no wording of its own; it
 * emphasises the segments that module marked as the user's own phrases.
 */
export function ScopeBanner({
  segments,
  note,
}: {
  segments: ScopeSegment[];
  /** A paper the user NAMED that returned nothing at all. */
  note?: string | null;
}) {
  return (
    <div className="flex items-start gap-1.5 text-xs text-muted-foreground">
      <Search className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <div className="min-w-0 space-y-0.5">
        <p>
          {segments.map((segment, i) =>
            segment.emphasis ? (
              <span key={i} className="font-medium text-foreground">
                {segment.text}
              </span>
            ) : (
              <span key={i}>{segment.text}</span>
            )
          )}
        </p>
        {/* Kept visible through streaming: the answer is being written from
            fewer papers than were asked for. */}
        {note && <p className="text-warning">{note}</p>}
      </div>
    </div>
  );
}
