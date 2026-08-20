"use client";

import { AlertTriangle, X } from "lucide-react";
import { firstErrorMessage } from "@/lib/latex-log";

interface LogPanelProps {
  log: string;
  onClose: () => void;
  /**
   * Where the error is, as the COMPILE SERVICE determined it -- cross-
   * checked against the tree it staged, corroborated by TeX's own `l.<n>`
   * context, and declined outright when the log is ambiguous (see
   * `analyse_log` in `latex-compiler/app.py`). This panel does NOT read
   * them out of `log`: two shipped attempts did exactly that and both
   * produced a confident jump into the wrong file.
   *
   * Both are null together whenever the compiler declined, and the jump
   * control is simply absent then. A missing jump is a mild
   * disappointment; a jump to the wrong file is the failure this whole
   * design exists to prevent.
   */
  errorFile: string | null;
  errorLine: number | null;
  onJumpToError: (line: number, file: string) => void;
}

export function LogPanel({ log, errorFile, errorLine, onClose, onJumpToError }: LogPanelProps) {
  // Headline only. Never a file, never a line -- see `latex-log.ts`.
  const message = firstErrorMessage(log);
  const canJump = errorFile !== null && errorLine !== null;

  return (
    <div className="flex max-h-56 flex-col border-t border-border bg-muted/40">
      <div className="flex items-center justify-between px-3 py-1.5">
        <div className="flex items-center gap-2 text-xs">
          <AlertTriangle className="size-3.5 text-destructive" />
          <span className="font-medium text-foreground">
            {message ?? "Compilation failed"}
            {canJump && (
              <button
                className="ml-2 underline underline-offset-2 hover:text-primary"
                onClick={() => onJumpToError(errorLine, errorFile)}
              >
                {/* Naming the file is not decoration: in a multi-file
                    project the blamed line is usually in a chapter, not in
                    whatever happens to be on screen. The path is printed
                    HERE and nowhere else -- the headline is the text AFTER
                    the `path:line:` prefix, so this never doubles it. */}
                {`${errorFile}:${errorLine}`}
              </button>
            )}
          </span>
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
