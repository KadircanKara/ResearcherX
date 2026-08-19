"use client";

import { useState } from "react";
import { FileCode, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { LatexDocument } from "@/lib/latex";

interface DocumentListProps {
  documents: LatexDocument[];
  selectedId: string | null;
  canEdit: boolean;
  onSelect: (id: string) => void;
  onCreate: (name: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

export function DocumentList({
  documents,
  selectedId,
  canEdit,
  onSelect,
  onCreate,
  onDelete,
}: DocumentListProps) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      await onCreate(trimmed);
      setName("");
      setCreating(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex w-56 shrink-0 flex-col gap-1 border-r border-border pr-3">
      <div className="flex items-center justify-between pb-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Documents
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="size-7 p-0"
          disabled={!canEdit}
          title={canEdit ? "New document" : "You need editor access to add a document"}
          onClick={() => setCreating(true)}
        >
          <Plus className="size-4" />
        </Button>
      </div>

      {creating && (
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
            if (e.key === "Escape") {
              setCreating(false);
              setName("");
            }
          }}
          onBlur={() => void submit()}
          placeholder="paper.tex"
          className="mb-1 w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
        />
      )}

      {documents.length === 0 && !creating && (
        <p className="py-2 text-sm text-muted-foreground">No documents yet.</p>
      )}

      {documents.map((doc) => (
        <div
          key={doc.id}
          className={cn(
            "group flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm",
            doc.id === selectedId
              ? "bg-muted font-medium text-foreground"
              : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
          )}
        >
          <button
            className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
            onClick={() => onSelect(doc.id)}
          >
            <FileCode className="size-3.5 shrink-0" />
            <span className="truncate">{doc.name}</span>
          </button>
          <button
            className="invisible shrink-0 text-muted-foreground hover:text-destructive group-hover:visible disabled:hidden"
            disabled={!canEdit}
            title="Delete document"
            onClick={() => void onDelete(doc.id)}
          >
            <Trash2 className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
