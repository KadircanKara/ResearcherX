"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { LatexCollision } from "@/lib/latex";
import {
  type ConflictAction,
  type ConflictState,
  clearOverride,
  decisions,
  initialState,
  problems,
  setDefault,
  setOverride,
} from "@/lib/latex-conflicts";

interface ConflictDialogProps {
  open: boolean;
  busy: boolean;
  title: string;
  description: string;
  collisions: LatexCollision[];
  /** Every path in the target tree, for validation. */
  taken: string[];
  onCancel: () => void;
  onConfirm: (decisions: { path: string; new_path: string }[]) => void;
}

// Rows beyond this many collapse behind "Show all N" -- a merge of a large
// archive can report dozens, and rendering all of them by default is what
// pushed the footer off-screen in earlier drafts of this dialog.
const COLLAPSE_AT = 5;

/**
 * The one conflict dialog every duplicate-name surface (upload, new file,
 * rename, import) shares. Holds exactly one `useState<ConflictState>` and
 * computes nothing itself -- every transition goes through `lib/latex-conflicts`,
 * and every displayed suggestion is the server's own, never recomputed here.
 *
 * Structure mirrors `import-dropzone.tsx`, the dialog in this folder already
 * fixed for the overflow bugs this one would otherwise repeat: a bounded
 * `DialogContent`, a `min-h-0 min-w-0` scroll body (a long path has no break
 * opportunity, so `min-w-0` is what actually contains it), and a footer that
 * lives outside the scroller.
 */
export function ConflictDialog({
  open,
  busy,
  title,
  description,
  collisions,
  taken,
  onCancel,
  onConfirm,
}: ConflictDialogProps) {
  const [state, setState] = useState<ConflictState>(initialState());
  const [expanded, setExpanded] = useState(false);

  // Reset when the dialog actually closes, the same way `import-dropzone.tsx`
  // does -- a second conflict (a different upload, a different rename) must
  // not inherit the previous one's per-row overrides.
  useEffect(() => {
    if (!open) {
      setState(initialState());
      setExpanded(false);
    }
  }, [open]);

  const rowProblems = problems(state, collisions, taken);
  const hasProblems = Object.keys(rowProblems).length > 0;
  // A problem on a COLLAPSED row disables Confirm with nothing on screen to
  // explain it, and no way to reach the row that caused it. `problems` is
  // computed over every collision (it has to be -- two hidden rows renamed
  // to the same thing is a real conflict), so the list force-expands
  // whenever a hidden row is the one holding Confirm down.
  const hiddenProblem = collisions
    .slice(COLLAPSE_AT)
    .some((c) => rowProblems[c.path] !== undefined);
  const showAll = expanded || hiddenProblem;
  const visible = showAll ? collisions : collisions.slice(0, COLLAPSE_AT);

  function rowAction(path: string): ConflictAction {
    return state.overrides[path]?.action ?? state.defaultAction;
  }

  function setRowAction(collision: LatexCollision, action: ConflictAction) {
    setState((s) => {
      // Falling back to the batch default is a real, distinct choice from
      // "this row is pinned to keep_both" -- clear the override rather than
      // writing one that merely restates the default, so a later batch
      // change (`setDefault`) still moves this row too.
      if (action === s.defaultAction) return clearOverride(s, collision.path);
      const current = s.overrides[collision.path];
      return setOverride(
        s,
        collision.path,
        action,
        action === "rename" ? (current?.newPath ?? collision.suggestion) : collision.suggestion
      );
    });
  }

  function confirm() {
    if (busy || hasProblems) return;
    onConfirm(decisions(state, collisions));
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {/* `min-w-0` is load-bearing: a flex item's automatic minimum size
            is its content, and a path like
            `Kadircan_MOO_Multi_UAV_Many_visit.tex` has no break opportunity,
            so without it the body refuses to shrink and the path spills
            straight past the dialog's edge. */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 overflow-y-auto">
          <div className="flex items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">For all:</span>
            <Button
              type="button"
              size="xs"
              variant={state.defaultAction === "keep_both" ? "secondary" : "ghost"}
              onClick={() => setState((s) => setDefault(s, "keep_both"))}
              disabled={busy}
            >
              Keep both
            </Button>
            <Button
              type="button"
              size="xs"
              variant={state.defaultAction === "rename" ? "secondary" : "ghost"}
              onClick={() => setState((s) => setDefault(s, "rename"))}
              disabled={busy}
            >
              Rename each
            </Button>
          </div>

          <div className="flex min-w-0 flex-col gap-2">
            {visible.map((collision) => {
              const action = rowAction(collision.path);
              const override = state.overrides[collision.path];
              const problem = rowProblems[collision.path];
              return (
                <div
                  key={collision.path}
                  className="flex min-w-0 flex-col gap-1.5 rounded-md border border-input px-2.5 py-2"
                >
                  <div className="flex min-w-0 items-center gap-2 text-xs">
                    <span
                      className="min-w-0 flex-1 truncate font-mono text-muted-foreground"
                      title={collision.existing}
                    >
                      {collision.existing}
                    </span>
                    <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
                    {action === "keep_both" ? (
                      <span
                        className="min-w-0 flex-1 truncate font-mono"
                        title={collision.suggestion}
                      >
                        {collision.suggestion}
                      </span>
                    ) : (
                      <Input
                        className="h-6 min-w-0 flex-1 font-mono text-xs"
                        value={override?.newPath ?? collision.suggestion}
                        title={override?.newPath ?? collision.suggestion}
                        disabled={busy}
                        onChange={(e) =>
                          setState((s) => setOverride(s, collision.path, "rename", e.target.value))
                        }
                      />
                    )}
                  </div>

                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1">
                      <Button
                        type="button"
                        size="xs"
                        variant={action === "keep_both" ? "secondary" : "ghost"}
                        onClick={() => setRowAction(collision, "keep_both")}
                        disabled={busy}
                      >
                        Keep both
                      </Button>
                      <Button
                        type="button"
                        size="xs"
                        variant={action === "rename" ? "secondary" : "ghost"}
                        onClick={() => setRowAction(collision, "rename")}
                        disabled={busy}
                      >
                        Rename
                      </Button>
                    </div>
                    {problem && (
                      <span className="truncate text-xs text-destructive" title={problem}>
                        {problem}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {collisions.length > COLLAPSE_AT && (
            <Button
              type="button"
              variant="ghost"
              className="self-start"
              onClick={() => setExpanded((e) => !e)}
              // Collapsing again would re-hide the row the user has to fix.
              disabled={hiddenProblem}
            >
              {showAll ? "Show fewer" : `Show all ${collisions.length}`}
            </Button>
          )}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" onClick={confirm} disabled={busy || hasProblems}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            {state.defaultAction === "keep_both" ? "Keep both" : "Rename each"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
