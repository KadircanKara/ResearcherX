"use client";

import { useEffect, useRef, useState } from "react";
import { findMentionQuery, insertMention, matchPapers, reconcileMentions, type Mention } from "@/lib/mentions";
import type { Paper } from "@/lib/types";

interface Props {
  value: string;
  onChange: (value: string) => void;
  mentions: Mention[];
  onMentionsChange: (mentions: Mention[]) => void;
  papers: Paper[];
  disabled?: boolean;
  onSubmit: () => void;
}

const LISTBOX_ID = "mention-listbox";
const optionId = (paperId: string) => `mention-option-${paperId}`;

export function MentionTextarea({
  value,
  onChange,
  mentions,
  onMentionsChange,
  papers,
  disabled,
  onSubmit,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [query, setQuery] = useState<{ query: string; start: number } | null>(null);
  const [active, setActive] = useState(0);

  const options = query ? matchPapers(papers, query.query) : [];
  const open = query !== null && options.length > 0;

  useEffect(() => setActive(0), [query?.query]);

  function update(next: string, caret: number) {
    onChange(next);
    // Offsets are never stored — the mention list is re-derived from the text
    // on every change, so an edit anywhere cannot leave a stale id behind.
    onMentionsChange(reconcileMentions(next, mentions));
    setQuery(findMentionQuery(next, caret));
  }

  // Defensive: `active` resets to 0 in an effect keyed on the query, one
  // render after `options` can shrink, so `options[active]` is transiently
  // undefined in theory. Resolve first and bail rather than ever passing
  // undefined into insertMention.
  function choose(paper: Paper | undefined) {
    const el = ref.current;
    if (!el || !query || !paper) return;
    const { text, caret } = insertMention(value, query.start, el.selectionStart, paper.title);
    onChange(text);
    onMentionsChange(
      reconcileMentions(text, [...mentions, { paperId: paper.id, title: paper.title }])
    );
    setQuery(null);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(caret, caret);
    });
  }

  const activeOption = open ? options[active] : undefined;

  return (
    <div className="relative flex-1">
      {open && (
        <ul
          id={LISTBOX_ID}
          role="listbox"
          aria-label="Papers"
          className="absolute bottom-full mb-1 max-h-60 w-full overflow-y-auto rounded-xl border border-border bg-popover p-1 shadow-lg"
        >
          {options.map((paper, i) => (
            <li key={paper.id}>
              <button
                id={optionId(paper.id)}
                type="button"
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(paper);
                }}
                className={`w-full truncate rounded-lg px-2 py-1.5 text-left text-sm ${
                  i === active ? "bg-muted" : "hover:bg-muted"
                }`}
              >
                {paper.title}
              </button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={ref}
        rows={2}
        value={value}
        disabled={disabled}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        aria-haspopup="listbox"
        aria-controls={LISTBOX_ID}
        aria-activedescendant={activeOption ? optionId(activeOption.id) : undefined}
        onChange={(e) => update(e.target.value, e.target.selectionStart)}
        onKeyDown={(e) => {
          if (open) {
            // While the dropdown is open Enter selects and MUST NOT submit —
            // except Shift+Enter, which must still insert a newline like a
            // plain textarea and leave the dropdown open.
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((i) => (i + 1) % options.length);
              return;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => (i - 1 + options.length) % options.length);
              return;
            }
            if ((e.key === "Enter" && !e.shiftKey) || e.key === "Tab") {
              e.preventDefault();
              choose(options[active]);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setQuery(null);
              return;
            }
          }
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSubmit();
          }
        }}
        onClick={(e) => setQuery(findMentionQuery(value, e.currentTarget.selectionStart))}
        onBlur={() => setQuery(null)}
        placeholder="Ask a follow-up question…  Type @ to scope to a paper"
        className="w-full resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
      />
    </div>
  );
}
