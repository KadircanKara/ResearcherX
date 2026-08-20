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
    <div className="rx-log">
      <div className="rx-log-head">
        <div className="flex items-start gap-2">
          <AlertTriangle className="size-3.5 shrink-0 text-destructive" style={{ marginTop: 3 }} />
          <span className="rx-log-msg">
            {message ?? "Compilation failed"}
            {canJump && (
              <button
                className="rx-jump"
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
        <button onClick={onClose} className="rx-icon-btn" title="Close the log">
          <X className="size-3.5" />
        </button>
      </div>
      {/*
        The LaTeX log is the user's OWN content, so it is shown verbatim. The
        project's "client-visible error text is generic" rule exists to keep
        server internals out of responses; it has nothing to say about the
        user's own "Undefined control sequence".
      */}
      <pre>{log}</pre>
    </div>
  );
}
