"use client";

import { Search, X } from "lucide-react";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  /** Announced to screen readers, which have no icon to read. */
  label: string;
}

/**
 * The search box shared by the papers, chat and LaTeX list pages.
 *
 * Presentational only, like `BulkEditBar`: each list owns its own query and
 * does its own filtering, so the three cannot drift into three different
 * search behaviours while looking identical.
 *
 * No debounce. The filter runs over a list already in memory, so it is a
 * synchronous array pass with nothing to wait for -- a debounce here would
 * add lag to a keystroke that costs nothing.
 */
export function SearchInput({ value, onChange, placeholder, label }: SearchInputProps) {
  return (
    <div className="relative min-w-0">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          // Escape clears without moving focus, so a mistyped query costs
          // one key rather than a reach for the mouse.
          if (e.key === "Escape" && value) {
            e.preventDefault();
            onChange("");
          }
        }}
        placeholder={placeholder}
        aria-label={label}
        className="h-8 w-full rounded-md border border-input bg-background py-1 pr-7 pl-7 text-sm placeholder:text-muted-foreground/60 focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-none"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-0.5 text-muted-foreground/60 transition-colors hover:bg-muted hover:text-foreground"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}
