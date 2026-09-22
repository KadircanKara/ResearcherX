import type { ReactNode } from "react";

/**
 * The header a project TAB owns: eyebrow, h1, right-hand meta lines and an
 * optional action.
 *
 * Distinct from `ProjectHeader`, which names the project itself and sits
 * above the tab strip — this one titles the tab's own content ("Library",
 * "Chat") and is shared by Papers and Chat so the two cannot drift into two
 * different header shapes.
 */
export function PageHeader({
  eyebrow,
  title,
  meta,
  actions,
}: {
  eyebrow?: string;
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        {eyebrow && (
          <p className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
            {eyebrow}
          </p>
        )}
        <h1 className="mt-1 text-xl font-semibold tracking-tight">{title}</h1>
      </div>
      {(meta || actions) && (
        <div className="flex shrink-0 items-start gap-3">
          {meta && (
            <div className="text-right text-[12px] leading-5 text-muted-foreground">{meta}</div>
          )}
          {actions}
        </div>
      )}
    </div>
  );
}
