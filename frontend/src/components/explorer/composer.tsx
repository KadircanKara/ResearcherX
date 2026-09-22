"use client";

import { useRef, useState } from "react";
import { ArrowUp, Mic } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { libraryPapers, projectScopes } from "@/lib/explorer-data";
import { insertMention, mentionOpen } from "@/lib/explorer";
import { cn } from "@/lib/utils";

/**
 * The scope picker. Explorer searches beyond ONE library, and which library it
 * scores against is the only setting a question carries — so it sits inside the
 * composer, next to the question, rather than in a settings panel somewhere.
 */
function ScopePicker({ className }: { className?: string }) {
  const [scope, setScope] = useState(projectScopes[0].value);

  return (
    // `items` is what lets `SelectValue` render the project's NAME; without it
    // Base UI has only the raw value to show, and the picker reads "uav".
    <Select
      items={projectScopes}
      value={scope}
      onValueChange={(next) => setScope(next ?? scope)}
    >
      <SelectTrigger
        aria-label="Library to score against"
        className={cn(
          "h-8 w-auto max-w-60 rounded-full border-0 bg-secondary text-xs shadow-none",
          className
        )}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {projectScopes.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * The question box, shared by the Explorer home page and the thread.
 *
 * ONE component rather than two arrangements of the same controls: the mention
 * rule, the Enter-to-send rule and the scope picker would otherwise exist twice
 * and drift. `variant` is only the frame around them — `page` sits in the flow
 * of the home page, `sticky` pins to the bottom of a thread.
 */
export function Composer({
  value,
  onChange,
  onSubmit,
  variant = "sticky",
  placeholder = "Ask a follow-up… Use @ to mention a paper",
  minHeightClass = "min-h-14",
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  variant?: "sticky" | "page";
  placeholder?: string;
  minHeightClass?: string;
}) {
  const [mentions, setMentions] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);

  function update(next: string) {
    onChange(next);
    setMentions(mentionOpen(next));
  }

  function pick(title: string) {
    update(insertMention(value, title));
    setMentions(false);
    field.current?.focus();
  }

  return (
    <div
      className={cn(
        "rounded-lg border bg-background p-2",
        variant === "sticky"
          ? "sticky bottom-4 z-10 shadow-lg"
          : "bg-card shadow-sm focus-within:ring-1 focus-within:ring-ring"
      )}
    >
      <div className="relative">
        <Textarea
          ref={field}
          value={value}
          onChange={(event) => update(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && mentions) {
              event.preventDefault();
              setMentions(false);
              return;
            }
            // Enter sends, Shift+Enter breaks the line — the convention every
            // other composer in this app follows.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSubmit();
            }
          }}
          placeholder={placeholder}
          className={cn(
            "resize-none border-0 text-base shadow-none focus-visible:ring-0",
            minHeightClass,
            // The sticky variant's send buttons sit inside the field; the
            // page variant's sit in the row below it.
            variant === "sticky" ? "pr-20" : "px-3"
          )}
        />
        {mentions ? (
          <div className="absolute bottom-full left-0 z-20 mb-2 w-80 max-w-full rounded-lg border bg-popover p-1 shadow-md">
            {libraryPapers.slice(0, 3).map((paper) => (
              <Button
                key={paper.title}
                variant="ghost"
                className="h-auto w-full justify-start whitespace-normal rounded-md px-3 py-2 text-left text-xs font-normal"
                onClick={() => pick(paper.title)}
              >
                {paper.title}
              </Button>
            ))}
          </div>
        ) : null}
        {variant === "sticky" ? (
          <div className="absolute bottom-2 right-2 flex gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-full"
              aria-label="Use microphone"
            >
              <Mic />
            </Button>
            <Button
              size="icon"
              className="size-8 rounded-full"
              aria-label="Send question"
              onClick={onSubmit}
            >
              <ArrowUp />
            </Button>
          </div>
        ) : null}
      </div>
      <div
        className={cn(
          "flex flex-wrap items-center gap-2 pt-2",
          variant === "sticky" ? "border-t" : "border-t px-1"
        )}
      >
        <ScopePicker className={variant === "sticky" ? "h-7 text-[11px]" : undefined} />
        {variant === "page" ? (
          <>
            <span className="ml-auto" />
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-full"
              aria-label="Use microphone"
            >
              <Mic />
            </Button>
            <Button
              size="icon"
              className="size-8 rounded-full"
              aria-label="Start exploration"
              onClick={onSubmit}
            >
              <ArrowUp />
            </Button>
          </>
        ) : null}
      </div>
    </div>
  );
}
