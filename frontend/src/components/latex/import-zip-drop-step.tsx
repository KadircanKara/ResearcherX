"use client";

import { useId, useState, type DragEvent } from "react";
import { UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/**
 * Step 1 of the import dialog: pick the archive (and, for a new project, its
 * name), then plan. Presentational -- `ImportDropzone` owns the request.
 */
export function ImportZipDropStep({
  mode,
  fileName,
  onFile,
  name,
  onNameChange,
  planning,
  canPlan,
  onPlan,
  error,
}: {
  mode: "new" | "merge";
  fileName: string | null;
  onFile: (file: File) => void;
  name: string;
  onNameChange: (value: string) => void;
  planning: boolean;
  /** A file is chosen and, for a new project, a name is typed. */
  canPlan: boolean;
  onPlan: () => void;
  /** The server's own message for a refused plan or commit, shown verbatim
   * (`errorText`): the user's archive, their quota, their path. */
  error: string | null;
}) {
  const [dragOver, setDragOver] = useState(false);
  const nameId = useId();

  function handleDrop(e: DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) onFile(dropped);
  }

  return (
    <div className="space-y-4">
      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={cn(
          "flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center transition-colors hover:bg-muted/40",
          dragOver && "bg-muted/40"
        )}
      >
        <UploadCloud className="size-6 text-muted-foreground" aria-hidden />
        {/* Truncated with the full name on hover: an underscore-joined
            archive name has no break opportunity and would otherwise spill
            past the dialog's edge. */}
        <span className="max-w-full truncate text-[13px] font-medium" title={fileName ?? undefined}>
          {fileName ?? "Drop a .zip here, or click to browse"}
        </span>
        {/* The backend's cap: `latex_project_max_bytes`. */}
        <span className="text-[12px] text-muted-foreground">.zip up to 25 MB</span>
        <input
          type="file"
          accept=".zip,application/zip"
          className="sr-only"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
            // Cleared so picking the same archive again still fires.
            e.target.value = "";
          }}
        />
      </label>

      {mode === "new" && (
        <div className="space-y-2">
          <Label htmlFor={nameId}>Name</Label>
          <Input
            id={nameId}
            value={name}
            maxLength={200}
            onChange={(e) => onNameChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canPlan && !planning) onPlan();
            }}
            placeholder="Project name"
          />
        </div>
      )}

      {error && <p className="break-words text-sm text-destructive">{error}</p>}

      <Button disabled={!canPlan || planning} onClick={onPlan} className="w-full">
        {planning ? "Planning import…" : "Plan import"}
      </Button>
    </div>
  );
}
