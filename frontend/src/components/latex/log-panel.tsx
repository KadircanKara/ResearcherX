"use client";

import { useState } from "react";
import { CheckCircle2, ChevronDown, Loader2, XCircle } from "lucide-react";
import { firstErrorMessage } from "@/lib/latex-log";
import type { CompileStatus } from "@/lib/latex-sync";
import { cn } from "@/lib/utils";

interface LogPanelProps {
  status: CompileStatus;
  /** The failed build's log when `status` is "failed", else the last good build's. */
  log: string | null;
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
  /**
   * A sync or jump message from the compile hook (already gated there, so a
   * "PDF is out of date" note never shows while the PDF is current). Shown
   * even before the first compile: declining a forward sync for a file
   * outside the main file's directory needs no build to be true.
   */
  note: string | null;
}

const META = {
  compiling: { icon: Loader2, spin: true, text: "Compiling…", tone: "text-muted-foreground" },
  success: { icon: CheckCircle2, spin: false, text: "Compiled successfully", tone: "text-emerald-600" },
  failed: { icon: XCircle, spin: false, text: "Compile failed", tone: "text-destructive" },
} as const;

/**
 * The compile status bar under both panes, ported from the prototype's
 * `LatexCompileLog`. It spans the source AND the preview on purpose: a
 * compile log is the only place a failed build explains itself, and inside
 * the preview it would be invisible in source-only mode -- exactly the mode
 * someone fixing an error is in.
 */
export function LogPanel({ status, log, errorFile, errorLine, onJumpToError, note }: LogPanelProps) {
  const [open, setOpen] = useState(false);

  if (status === "idle") {
    if (!note) return null;
    return (
      <div className="border-t bg-card">
        <p className="px-3 py-1.5 text-[12px] text-muted-foreground">{note}</p>
      </div>
    );
  }

  const meta = META[status];
  const Icon = meta.icon;
  const expandable = status !== "compiling" && Boolean(log);
  // Headline only. Never a file, never a line -- see `latex-log.ts`.
  const headline = status === "failed" && log ? firstErrorMessage(log) : null;
  const canJump = status === "failed" && errorFile !== null && errorLine !== null;

  return (
    <div className="border-t bg-card">
      <div className="flex w-full items-center gap-2 px-3 py-1.5 text-[12px]">
        <button
          type="button"
          onClick={() => expandable && setOpen((v) => !v)}
          aria-expanded={expandable ? open : undefined}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <Icon className={cn("size-3.5 shrink-0", meta.tone, meta.spin && "animate-spin")} aria-hidden />
          <span className={cn("shrink-0", meta.tone)}>{meta.text}</span>
          {headline && <span className="truncate text-muted-foreground">{headline}</span>}
          {note && <span className="truncate text-muted-foreground">{note}</span>}
        </button>
        {canJump && (
          <button
            type="button"
            onClick={() => onJumpToError(errorLine, errorFile)}
            className="shrink-0 font-mono text-[11px] text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            {/* Naming the file is not decoration: in a multi-file project
                the blamed line is usually in a chapter, not in whatever
                happens to be on screen. The path is printed HERE and nowhere
                else -- the headline is the text AFTER the `path:line:`
                prefix, so this never doubles it. */}
            {`${errorFile}:${errorLine}`}
          </button>
        )}
        {expandable && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? "Hide the compile log" : "Show the compile log"}
            className="shrink-0"
          >
            <ChevronDown
              className={cn("size-3.5 text-muted-foreground transition-transform", open && "rotate-180")}
              aria-hidden
            />
          </button>
        )}
      </div>
      {/*
        The LaTeX log is the user's OWN content, so it is shown verbatim. The
        project's "client-visible error text is generic" rule exists to keep
        server internals out of responses; it has nothing to say about the
        user's own "Undefined control sequence".
      */}
      {open && expandable && (
        <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap border-t bg-muted/30 p-3 font-mono text-[11px] text-muted-foreground">
          {log}
        </pre>
      )}
    </div>
  );
}
