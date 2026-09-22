/**
 * What the turn in flight is doing right now: reading the question or
 * searching N papers. Hidden once the answer starts arriving, as in the
 * prototype — the words appearing say that on their own.
 *
 * The wording is `statusLabel`'s (`lib/chat-scope.ts`), driven by the
 * backend's five SSE events and nothing else — this renders a phase, it
 * never guesses one.
 */
export function StatusLine({ label }: { label: string }) {
  return (
    <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <span className="h-2 w-2 animate-pulse rounded-full bg-primary" aria-hidden />
      <span>{label}</span>
    </div>
  );
}
