"use client";

import { Plus } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ResultCardData {
  title: string;
  meta: string;
  abstract: string;
  matched: string[];
  missing: string[];
  neutral?: string[];
  relevance: number;
}

export function ResultCard({
  title,
  meta,
  abstract,
  matched,
  missing,
  neutral = [],
  relevance,
}: ResultCardData) {
  return (
    <div className="grid grid-cols-1 gap-5 rounded-2xl border border-border bg-card p-5 transition-colors hover:border-primary/30 sm:grid-cols-[1fr_180px]">
      {/* Left: paper details */}
      <div>
        <h3 className="font-serif text-[17px] font-semibold leading-snug">
          {title}
        </h3>
        <p className="mt-1.5 font-mono text-[11.5px] text-muted-foreground">
          {meta}
        </p>
        <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
          {abstract}
        </p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {matched.map((k) => (
            <span
              key={`m-${k}`}
              className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-600 dark:text-emerald-400"
            >
              ✓ {k}
            </span>
          ))}
          {missing.map((k) => (
            <span
              key={`x-${k}`}
              className="rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted-foreground"
            >
              ✕ {k}
            </span>
          ))}
          {neutral.map((k) => (
            <span
              key={`n-${k}`}
              className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
            >
              {k}
            </span>
          ))}
        </div>
      </div>

      {/* Right: relevance + actions */}
      <div className="flex flex-col">
        <span className="font-mono text-[11px] text-muted-foreground">
          relevance
        </span>
        <span className="text-gradient font-mono text-base font-semibold">
          {relevance}%
        </span>
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full gradient-bar"
            style={{ width: `${relevance}%` }}
          />
        </div>
        <div className="mt-3 flex flex-col gap-2">
          <button
            type="button"
            className={cn(
              "inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition hover:shadow-glow"
            )}
          >
            <Plus className="size-3.5" />
            Add to project
          </button>
          <button
            type="button"
            className="inline-flex h-9 items-center justify-center rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-muted"
          >
            Open
          </button>
        </div>
      </div>
    </div>
  );
}
