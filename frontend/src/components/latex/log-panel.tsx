"use client";

import { AlertTriangle, X } from "lucide-react";
import { firstError } from "@/lib/latex-log";

interface LogPanelProps {
  log: string;
  onClose: () => void;
  /**
   * `file` is the tree-relative file the compiler NAMED on the error line
   * (`-file-line-error` makes every located error self-describing), or null
   * when the error was one TeX raised with no file position to report. It
   * is passed on rather than resolved here: this panel knows nothing about
   * which files exist, and the decision to decline a jump belongs with
   * whoever does.
   *
   * `line` and `file` arrive together or not at all, so the button below is
   * never offered with half an address.
   */
  onJumpToError: (line: number, file: string | null) => void;
}

export function LogPanel({ log, onClose, onJumpToError }: LogPanelProps) {
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
                  onClick={() => onJumpToError(error.line as number, error.file)}
                >
                  {/* Naming the file is not decoration: in a multi-file
                      project the blamed line is usually in a chapter, not in
                      whatever happens to be on screen. The path is printed
                      HERE and nowhere else -- `error.message` is the text
                      AFTER the `path:line:` prefix, so this never doubles
                      it. */}
                  {error.file ? `${error.file}:${error.line}` : `line ${error.line}`}
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
