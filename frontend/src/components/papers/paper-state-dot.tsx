import type { PaperState } from "@/lib/papers";
import { cn } from "@/lib/utils";

/**
 * The State cell: a dot and the state's own word.
 *
 * Both come from `lib/papers.ts` — this component picks no vocabulary and
 * makes no claim of its own, it only decides which colour a tone is drawn
 * in. The pulse is on `checking` alone, because that is the only state that
 * is going to change on its own.
 */
export function PaperStateDot({ state }: { state: PaperState }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px]">
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          state.tone === "on"
            ? "bg-positive"
            : state.tone === "bad"
              ? "bg-destructive"
              : "bg-muted-foreground/60",
          state.kind === "checking" && "animate-pulse"
        )}
        aria-hidden
      />
      <span className={state.tone === "bad" ? "text-destructive" : "text-foreground"}>
        {state.label}
      </span>
    </span>
  );
}
