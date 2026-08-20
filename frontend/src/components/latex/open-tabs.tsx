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

  // Deliberately NOT role="tablist"/"tab": each tab is a wrapper holding a
  // select button, a dirty dot and a close button, and a tablist whose
  // children are not tabs is a worse answer for a screen reader than no roles
  // at all. `aria-current` is valid on any element and says the one thing
  // that matters here.
  return (
    <div className="rx-tabs">
      {paths.map((path) => {
        const label = (counts.get(basename(path)) ?? 0) > 1 ? path : basename(path);
        const isActive = path === activePath;
        return (
          <div key={path} title={path} className={cn("rx-tab", isActive && "rx-tab-on")}>
            <button
              aria-current={isActive ? "true" : undefined}
              className="rx-tab-label"
              onClick={() => onSelect(path)}
            >
              {label}
            </button>
            {dirty.has(path) && <span className="rx-unsaved" title="Unsaved changes" />}
            <button title="Close" className="rx-tab-x" onClick={() => onClose(path)}>
              <X className="size-3" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
