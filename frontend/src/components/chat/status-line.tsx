/**
 * What the turn in flight is doing right now: reading the question,
 * searching N papers, writing the answer.
 *
 * The wording is `statusLabel`'s (`lib/chat-scope.ts`), driven by the
 * backend's five SSE events and nothing else — this renders a phase, it
 * never guesses one.
 */
export function StatusLine({ label }: { label: string }) {
  return (
    <p role="status" className="flex items-center gap-2 text-[13px] text-muted-foreground">
      <span className="size-2 shrink-0 animate-pulse rounded-full bg-primary" aria-hidden />
      {label}
    </p>
  );
}
