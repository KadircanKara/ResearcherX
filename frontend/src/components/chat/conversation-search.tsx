"use client";

import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";

/** The conversation list's filter box: the prototype's Input with a leading
 *  search icon. What it matches is `lib/search.ts`'s rule, applied by the page. */
export function ConversationSearch({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="relative max-w-sm">
      <Search
        className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
        aria-hidden
      />
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search conversations"
        aria-label="Search conversations"
        className="pl-8"
      />
    </div>
  );
}
