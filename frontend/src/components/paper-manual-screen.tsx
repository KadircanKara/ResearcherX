"use client";

import { Input } from "@/components/ui/input";

export const TITLE_MAX = 150;
const TITLE_WARN = 120;

export type PaperFields = { title: string; abstract: string; body: string };

function FieldLabel({ label }: { label: string }) {
  return <p className="text-xs font-medium text-muted-foreground">{label}</p>;
}

function CharCounter({ value }: { value: string }) {
  if (value.length <= TITLE_WARN) return null;
  return (
    <p className="text-right text-xs text-muted-foreground">
      {TITLE_MAX - value.length} chars left
    </p>
  );
}

const TEXTAREA_CLASS =
  "w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export function PaperManualScreen({
  value,
  onChange,
  disabled = false,
  readOnlyContent = false,
}: {
  value: PaperFields;
  onChange: (next: PaperFields) => void;
  disabled?: boolean;
  readOnlyContent?: boolean;
}) {
  const set = (patch: Partial<PaperFields>) => onChange({ ...value, ...patch });

  return (
    <div className="flex flex-col gap-4">
      <div className="space-y-1">
        <FieldLabel label="Title" />
        <Input
          placeholder="Paper title"
          value={value.title}
          maxLength={TITLE_MAX}
          disabled={disabled}
          onChange={(e) => set({ title: e.target.value })}
        />
        <CharCounter value={value.title} />
      </div>

      <div className="space-y-1">
        <FieldLabel label="Abstract" />
        <textarea
          value={value.abstract}
          onChange={(e) => set({ abstract: e.target.value })}
          placeholder="Abstract…"
          disabled={disabled}
          readOnly={readOnlyContent}
          rows={3}
          className={TEXTAREA_CLASS + (readOnlyContent ? " opacity-60" : "")}
        />
      </div>

      <div className="space-y-1">
        <FieldLabel label="Body" />
        <textarea
          value={value.body}
          onChange={(e) => set({ body: e.target.value })}
          placeholder="Paper body text…"
          disabled={disabled}
          readOnly={readOnlyContent}
          rows={6}
          className={TEXTAREA_CLASS + (readOnlyContent ? " opacity-60" : "")}
        />
      </div>

      {readOnlyContent && (
        <p className="text-xs text-muted-foreground">
          Abstract and body were extracted from the source and can&apos;t be edited.
        </p>
      )}
    </div>
  );
}
