import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/**
 * The "there is nothing here" panel the Papers and Chat lists share.
 *
 * Presentational only. What the panel SAYS is the caller's — the two screens
 * explain two different absences — but a list that is empty and a list that
 * is merely filtered must never look the same, which is why `NoMatchState`
 * below is a separate component rather than a prop on this one.
 */
export function EmptyState({
  icon: Icon,
  title,
  body,
  children,
}: {
  icon?: LucideIcon;
  title: string;
  body?: string;
  children?: ReactNode;
}) {
  return (
    <div className="fade-block rounded-lg border border-dashed bg-card/40 px-6 py-12 text-center">
      {Icon && <Icon className="mx-auto mb-3 size-6 text-muted-foreground" aria-hidden />}
      <p className="text-sm font-medium text-foreground">{title}</p>
      {body && <p className="mx-auto mt-2 max-w-md text-[13px] text-muted-foreground">{body}</p>}
      {children && <div className="mt-4 flex justify-center">{children}</div>}
    </div>
  );
}

/**
 * A query that matched nothing.
 *
 * Said out loud on purpose: an empty table under a filled search box
 * otherwise reads as the library — or the conversation list — having emptied
 * itself.
 */
export function NoMatchState({ query, noun }: { query: string; noun: string }) {
  return (
    <div className="fade-block rounded-lg border border-dashed px-6 py-10 text-center text-[13px] text-muted-foreground">
      No {noun} match “{query}”.
    </div>
  );
}
