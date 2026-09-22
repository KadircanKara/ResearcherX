"use client";

import type { ReactNode } from "react";
import { MentionTextarea } from "@/components/chat/mention-textarea";
import { Button } from "@/components/ui/button";
import type { Mention } from "@/lib/mentions";
import type { Paper } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The box both Chat screens ask from: the `@`-aware textarea, the hint under
 * it and the send button.
 *
 * The mention rules all live in `lib/mentions.ts` and `MentionTextarea` —
 * `@` opens only at a word start, a picked title is inserted into the text
 * in full (never truncated, so the prompt reads as what the user sees) and
 * an 11th pick is refused visibly. This component owns none of that; it owns
 * the frame around it.
 */
const HELPER = (
  <>
    Type <b className="font-semibold text-foreground">@</b> to name a paper and search only
    inside it
  </>
);

export function Composer({
  papers,
  value,
  onChange,
  mentions,
  onMentionsChange,
  onSubmit,
  disabled = false,
  submitLabel,
  placeholder,
  helper = HELPER,
  error,
  className,
}: {
  papers: Paper[];
  value: string;
  onChange: (value: string) => void;
  mentions: Mention[];
  onMentionsChange: (mentions: Mention[]) => void;
  onSubmit: () => void;
  disabled?: boolean;
  submitLabel: string;
  placeholder?: string;
  helper?: ReactNode;
  error?: string | null;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2 rounded-lg border bg-card p-2", className)}>
      <MentionTextarea
        value={value}
        onChange={onChange}
        mentions={mentions}
        onMentionsChange={onMentionsChange}
        papers={papers}
        disabled={disabled}
        onSubmit={onSubmit}
        {...(placeholder !== undefined ? { placeholder } : {})}
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] text-muted-foreground">{helper}</p>
        <div className="flex items-center gap-2">
          {error && (
            <span role="status" className="text-[12px] text-destructive">
              {error}
            </span>
          )}
          <Button size="sm" onClick={onSubmit} disabled={disabled || !value.trim()}>
            {submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
