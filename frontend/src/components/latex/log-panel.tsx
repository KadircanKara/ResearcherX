"use client";

import { AlertTriangle, X } from "lucide-react";
import { firstError } from "@/lib/latex-log";

interface LogPanelProps {
  log: string;
  onClose: () => void;
  onJumpToLine: (line: number) => void;
}

export function LogPanel({ log, onClose, onJumpToLine }: LogPanelProps) {
  const error = firstError(log);

  return (
    <div className="flex max-h-56 flex-col border-t border-border bg-muted/40">
      <div className="flex items-center justify-between px-3 py-1.5">
        <div className="flex items-center gap-2 text-xs">
          <AlertTriangle className="size-3.5 text-destructive" />
          {error ? (
            <span className="font-medium text-foreground">
              {error.message}
              {error.line !== null && (
                <button
                  className="ml-2 underline underline-offset-2 hover:text-primary"
                  onClick={() => onJumpToLine(error.line as number)}
                >
                  line {error.line}
                </button>
              )}
            </span>
          ) : (
            <span className="font-medium text-foreground">Compilation failed</span>
          )}
        </div>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
          <X className="size-3.5" />
        </button>
      </div>
      {/*
        The LaTeX log is the user's OWN content, so it is shown verbatim. The
        project's "client-visible error text is generic" rule exists to keep
        server internals out of responses; it has nothing to say about the
        user's own "Undefined control sequence".
      */}
      <pre className="overflow-auto px-3 pb-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
        {log}
      </pre>
    </div>
  );
}
