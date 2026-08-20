"use client";

import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface OpenTabsProps {
  paths: string[];
  activePath: string | null;
  /** The engine owns the truth here; this is rendered as a dot, nothing more. */
  dirtyPaths: string[];
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
}

function basename(path: string): string {
  const slash = path.lastIndexOf("/");
  return slash === -1 ? path : path.slice(slash + 1);
}

export function OpenTabs({ paths, activePath, dirtyPaths, onSelect, onClose }: OpenTabsProps) {
  // A tab shows the basename -- `intro.tex` is what the user is looking for,
  // `chapters/intro.tex` doesn't fit in a tab. But two open files CAN share a
  // basename (chapters/intro.tex, appendix/intro.tex), and showing "intro.tex"
  // twice with no way to tell them apart is worse than a longer label, so any
  // basename shared by more than one open path falls back to the full path
  // for every tab holding it. Derived fresh from `paths` on every render --
  // no new state, since it's a pure function of what's currently open.
  const counts = new Map<string, number>();
  for (const p of paths) counts.set(basename(p), (counts.get(basename(p)) ?? 0) + 1);

  const dirty = new Set(dirtyPaths);

  return (
    <div className="flex h-8 shrink-0 items-center gap-0.5 overflow-x-auto border-b border-border px-1">
      {paths.map((path) => {
        const label = (counts.get(basename(path)) ?? 0) > 1 ? path : basename(path);
        const isActive = path === activePath;
        return (
          <div
            key={path}
            title={path}
            className={cn(
              "group flex shrink-0 items-center gap-1.5 rounded-t-md border-b-2 px-2.5 py-1 text-sm",
              isActive
                ? "border-primary bg-muted font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            )}
          >
            <button className="max-w-40 truncate" onClick={() => onSelect(path)}>
              {label}
            </button>
            {dirty.has(path) && (
              <span
                className="size-1.5 shrink-0 rounded-full bg-foreground/60"
                title="Unsaved changes"
              />
            )}
            <button
              title="Close"
              className="shrink-0 text-muted-foreground/70 hover:text-foreground"
              onClick={() => onClose(path)}
            >
              <X className="size-3" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
