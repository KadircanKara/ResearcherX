"use client";

import { Mic, Send } from "lucide-react";

interface ChatBoxProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
}

export function ChatBox({ value, onChange, onSubmit, placeholder }: ChatBoxProps) {
  const empty = value.trim().length === 0;

  return (
    <div className="rounded-2xl border border-border bg-card p-4 text-left shadow-sm transition focus-within:border-primary/50 focus-within:shadow-glow">
      <textarea
        rows={1}
        aria-label="Research query"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (!empty) onSubmit();
          }
        }}
        placeholder={placeholder ?? "Ask anything…"}
        className="w-full resize-none bg-transparent text-[15px] outline-none placeholder:text-muted-foreground"
      />
      <div className="mt-2 flex items-center justify-between">
        <button
          type="button"
          aria-label="Voice input"
          className="grid size-9 place-items-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Mic className="size-4" />
        </button>
        <button
          type="button"
          onClick={() => {
            if (!empty) onSubmit();
          }}
          disabled={empty}
          className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-3.5 text-sm font-medium text-primary-foreground transition hover:shadow-glow disabled:opacity-50 disabled:hover:shadow-none"
        >
          <Send className="size-3.5" />
          Send
        </button>
      </div>
    </div>
  );
}
