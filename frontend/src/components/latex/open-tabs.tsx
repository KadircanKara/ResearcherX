"use client";

import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { basename } from "@/lib/latex-tree";

interface OpenTabsProps {
  paths: string[];
  activePath: string | null;
  /** The engine owns the truth here; this is rendered as a dot, nothing more. */
  dirtyPaths: string[];
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
}

/** The open-files bar above the editor, ported from the prototype's `LatexTabsBar`. */
export function OpenTabs({ paths, activePath, dirtyPaths, onSelect, onClose }: OpenTabsProps) {
  if (paths.length === 0) {
    return (
      <div className="flex h-9 items-center border-b px-3 text-[12px] text-muted-foreground">
        No files open
      </div>
    );
  }

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
    <div role="tablist" aria-label="Open files" className="flex h-9 shrink-0 items-stretch overflow-x-auto border-b">
      {paths.map((path) => {
        const label = (counts.get(basename(path)) ?? 0) > 1 ? path : basename(path);
        const isActive = path === activePath;
        return (
          <div
            key={path}
            role="tab"
            aria-selected={isActive}
            title={path}
            className={cn(
              "group flex shrink-0 items-center gap-1.5 border-r px-2.5 text-[12px]",
              isActive ? "bg-card text-foreground" : "text-muted-foreground hover:bg-muted"
            )}
          >
            <button type="button" onClick={() => onSelect(path)} className="flex items-center gap-1.5 py-1.5">
              <span className="font-mono">{label}</span>
              {dirty.has(path) && (
                <span aria-label="Unsaved changes" className="size-1.5 rounded-full bg-foreground/70" />
              )}
            </button>
            <button
              type="button"
              aria-label={`Close ${basename(path)}`}
              onClick={() => onClose(path)}
              className="rounded p-0.5 opacity-0 hover:bg-muted-foreground/20 focus-visible:opacity-100 group-hover:opacity-100"
            >
              <X className="size-3" aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
