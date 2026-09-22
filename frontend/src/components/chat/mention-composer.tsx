"use client";

import { useId, useRef, useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  findMentionQuery,
  insertMention,
  matchPapers,
  MAX_MENTIONS,
  reconcileMentions,
  removeMention,
  withMention,
  type Mention,
} from "@/lib/mentions";
import type { Paper } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The `@`-aware box both Chat screens ask from, drawn as the app prototype's.
 *
 * Where it deliberately differs from the prototype: a picked paper's FULL
 * title is inserted into the text (never truncated, never removed), because
 * the text is what the model reads and what the user bubble shows — "the
 * prompt reads what the user sees". The prototype's chips are kept, but as a
 * mirror of the mentions that text holds: removing a chip removes that
 * `@title` from the text, and editing the text re-derives the chips. Every
 * rule lives in `lib/mentions.ts`; this component only wires them up.
 */
export function MentionComposer({
  papers,
  value,
  onChange,
  mentions,
  onMentionsChange,
  onSubmit,
  submitting = false,
  submitLabel = "Ask",
  placeholder = "Ask a question… use @ to mention a paper",
  helperText,
  popupPlacement = "below",
}: {
  papers: Paper[];
  value: string;
  onChange: (value: string) => void;
  mentions: Mention[];
  onMentionsChange: (mentions: Mention[]) => void;
  onSubmit: () => void;
  submitting?: boolean;
  submitLabel?: string;
  placeholder?: string;
  helperText?: string;
  /**
   * "above" for a composer pinned to the bottom of the viewport, where a
   * list opening downward would open off-screen.
   */
  popupPlacement?: "below" | "above";
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const listboxId = useId();
  const [popup, setPopup] = useState<{ query: string; start: number } | null>(null);
  const [highlight, setHighlight] = useState(0);

  const suggestions = popup ? matchPapers(papers, popup.query) : [];
  const optionId = (i: number) => `${listboxId}-option-${i}`;

  function update(next: string, caret: number) {
    onChange(next);
    // Offsets are never stored — the mention list is re-derived from the text
    // on every change, so an edit anywhere cannot leave a stale id behind.
    onMentionsChange(reconcileMentions(next, mentions));
    setPopup(findMentionQuery(next, caret));
    setHighlight(0);
  }

  function pick(paper: Paper | undefined) {
    const el = textareaRef.current;
    if (!paper || !popup || !el) return;
    // The server caps scope at MAX_MENTIONS and answers an 11th with a 422.
    // The cap note below is already showing, so the pick just closes.
    const isNew = !mentions.some((m) => m.paperId === paper.id);
    if (isNew && mentions.length >= MAX_MENTIONS) {
      setPopup(null);
      return;
    }
    const { text, caret } = insertMention(value, popup.start, el.selectionStart, paper.title);
    onChange(text);
    onMentionsChange(reconcileMentions(text, withMention(mentions, paper)));
    setPopup(null);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(caret, caret);
    });
  }

  function remove(index: number) {
    const next = removeMention(value, mentions, index);
    onChange(next);
    onMentionsChange(reconcileMentions(next, mentions.filter((_, i) => i !== index)));
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (popup) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlight((h) => Math.min(h + 1, Math.max(suggestions.length - 1, 0)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlight((h) => Math.max(h - 1, 0));
        return;
      }
      // While the list is open Enter picks and never sends. Shift+Enter
      // still inserts a newline, like a plain textarea.
      if ((e.key === "Enter" && !e.shiftKey) || (e.key === "Tab" && suggestions.length > 0)) {
        e.preventDefault();
        pick(suggestions[highlight]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setPopup(null);
        return;
      }
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim() && !submitting) onSubmit();
    }
  }

  const active = popup && suggestions[highlight] ? optionId(highlight) : undefined;

  return (
    <div className="space-y-2">
      <div className="relative">
        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => update(e.target.value, e.target.selectionStart ?? e.target.value.length)}
          onKeyDown={handleKeyDown}
          onBlur={() => setPopup(null)}
          placeholder={placeholder}
          disabled={submitting}
          rows={3}
          role="combobox"
          aria-expanded={!!popup}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-controls={popup ? listboxId : undefined}
          aria-activedescendant={active}
        />
        {popup && (
          <div
            id={listboxId}
            role="listbox"
            aria-label="Mention a paper"
            className={cn(
              "absolute z-20 w-full max-w-md rounded-md border bg-popover p-1 shadow-md",
              popupPlacement === "above" ? "bottom-full mb-1" : "mt-1"
            )}
          >
            {suggestions.length === 0 ? (
              <p className="px-2 py-1.5 text-[13px] text-muted-foreground">
                No papers match “{popup.query}”.
              </p>
            ) : (
              suggestions.map((paper, i) => (
                <button
                  key={paper.id}
                  id={optionId(i)}
                  type="button"
                  role="option"
                  aria-selected={i === highlight}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pick(paper);
                  }}
                  onMouseEnter={() => setHighlight(i)}
                  className={cn(
                    "flex w-full flex-col items-start rounded px-2 py-1.5 text-left text-[13px]",
                    i === highlight && "bg-accent"
                  )}
                >
                  <span className="truncate font-medium">{paper.title}</span>
                </button>
              ))
            )}
          </div>
        )}
      </div>

      {mentions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {mentions.map((m, i) => (
            <span
              key={m.paperId}
              className="inline-flex max-w-full items-center gap-1 rounded-full border bg-muted px-2 py-0.5 text-[12px]"
            >
              <span className="truncate">{m.title}</span>
              <button
                type="button"
                aria-label={`Remove mention ${m.title}`}
                disabled={submitting}
                onClick={() => remove(i)}
                className="shrink-0 rounded-full text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                <X className="size-3" aria-hidden />
              </button>
            </span>
          ))}
        </div>
      )}

      {mentions.length >= MAX_MENTIONS && (
        <p className="text-[12px] text-muted-foreground">
          You can mention up to {MAX_MENTIONS} papers.
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        {helperText ? (
          <p className="text-[12px] text-muted-foreground">{helperText}</p>
        ) : (
          <span />
        )}
        <Button size="sm" onClick={onSubmit} disabled={submitting || !value.trim()}>
          {submitting ? "Sending…" : submitLabel}
        </Button>
      </div>
    </div>
  );
}
