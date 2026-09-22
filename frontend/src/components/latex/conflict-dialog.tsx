"use client";

import {
  useEffect,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { Loader2 } from "lucide-react";
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
import { cn } from "@/lib/utils";

type SetConflictState = Dispatch<SetStateAction<ConflictState>>;

/** How a row RENDERS: its own override, else the batch default. This is the
 * one place `ConflictState.defaultAction` is read -- it picks static text or
 * an editable field, and never changes what `decisions` resolves to. */
function rowAction(state: ConflictState, path: string): ConflictAction {
  return state.overrides[path]?.action ?? state.defaultAction;
}

/**
 * One row switched to `action`.
 *
 * Falling back to the batch default is a real, distinct choice from "this row
 * is pinned to keep_both" -- clear the override rather than writing one that
 * merely restates the default, so a later "Apply to all" still moves this row
 * too. A row switched to Rename keeps whatever the user already typed.
 */
function withRowAction(
  state: ConflictState,
  collision: LatexCollision,
  action: ConflictAction
): ConflictState {
  if (action === state.defaultAction) return clearOverride(state, collision.path);
  const current = state.overrides[collision.path];
  return setOverride(
    state,
    collision.path,
    action,
    action === "rename" ? (current?.newPath ?? collision.suggestion) : collision.suggestion
  );
}

/** The value a row's rename field shows: what was typed, else the server's
 * suggestion -- never a suggestion computed here. */
function renameValue(state: ConflictState, collision: LatexCollision): string {
  return state.overrides[collision.path]?.newPath ?? collision.suggestion;
}

/**
 * One colliding path: which file, what it collides with, Keep both / Rename,
 * and where it will land.
 */
function FileConflictRow({
  collision,
  state,
  setState,
  problem,
  busy,
}: {
  collision: LatexCollision;
  state: ConflictState;
  setState: SetConflictState;
  problem: string | undefined;
  busy: boolean;
}) {
  const action = rowAction(state, collision.path);
  return (
    <div className="rounded-md border p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-mono text-[12px] text-foreground" title={collision.path}>
            {collision.path}
          </p>
          <p className="mt-0.5 break-words text-[12px] text-muted-foreground">
            already exists as <span className="font-mono">{collision.existing}</span>
          </p>
        </div>
        <div className="flex overflow-hidden rounded-md border text-[12px]">
          <button
            type="button"
            disabled={busy}
            className={cn(
              "px-2.5 py-1 transition-colors",
              action === "keep_both" ? "bg-primary text-primary-foreground" : "hover:bg-muted"
            )}
            onClick={() => setState((s) => withRowAction(s, collision, "keep_both"))}
          >
            Keep both
          </button>
          <button
            type="button"
            disabled={busy}
            className={cn(
              "border-l px-2.5 py-1 transition-colors",
              action === "rename" ? "bg-primary text-primary-foreground" : "hover:bg-muted"
            )}
            onClick={() => setState((s) => withRowAction(s, collision, "rename"))}
          >
            Rename
          </button>
        </div>
      </div>
      <div className="mt-2">
        {action === "keep_both" ? (
          <p className="truncate font-mono text-[12px] text-muted-foreground" title={collision.suggestion}>
            → {collision.suggestion}
          </p>
        ) : (
          <Input
            aria-label={`New path for ${collision.path}`}
            value={renameValue(state, collision)}
            disabled={busy}
            onChange={(e) =>
              setState((s) => setOverride(s, collision.path, "rename", e.target.value))
            }
            className="h-8 font-mono text-[12px]"
          />
        )}
        {problem && <p className="mt-1 text-[12px] text-destructive">{problem}</p>}
      </div>
    </div>
  );
}

/**
 * The per-file conflict rows, shared by `ConflictDialog` and the import
 * dialog's plan step.
 *
 * `problems` is computed over EVERY collision, not just the rendered `rows`:
 * two hidden rows renamed onto the same path are a real conflict too.
 */
export function FileConflictList({
  collisions,
  rows = collisions,
  state,
  setState,
  taken,
  busy = false,
}: {
  collisions: LatexCollision[];
  /** The rows to render, when a caller collapses the list. Defaults to all. */
  rows?: LatexCollision[];
  state: ConflictState;
  setState: SetConflictState;
  /** Every path already in the target tree. */
  taken: string[];
  busy?: boolean;
}) {
  const rowProblems = problems(state, collisions, taken);
  return (
    <div className="space-y-2">
      {rows.map((c) => (
        <FileConflictRow
          key={c.path}
          collision={c}
          state={state}
          setState={setState}
          problem={rowProblems[c.path]}
          busy={busy}
        />
      ))}
    </div>
  );
}

/**
 * The keep-both / rename question for one taken document NAME, shared by
 * `ConflictDialog`'s "name" variant and the import dialog's plan step.
 * Driven by the same `ConflictState` as a file row, with the name as a
 * one-row collision whose suggestion is the server's.
 */
export function NameConflictFields({
  collision,
  state,
  setState,
  taken,
  busy = false,
}: {
  collision: LatexCollision;
  state: ConflictState;
  setState: SetConflictState;
  /** Every document name already in the project. */
  taken: string[];
  busy?: boolean;
}) {
  const mode = rowAction(state, collision.path);
  const problem = problems(state, [collision], taken)[collision.path];
  return (
    <div className="space-y-3">
      <div className="rounded-md border bg-muted/40 p-2.5 text-[13px]">
        <p className="break-words text-muted-foreground">
          Already used by{" "}
          <span className="font-medium text-foreground">“{collision.existing}”</span>
        </p>
      </div>
      <div className="flex overflow-hidden rounded-md border text-[13px]">
        <button
          type="button"
          disabled={busy}
          className={cn(
            "flex-1 px-3 py-1.5 transition-colors",
            mode === "keep_both" ? "bg-primary text-primary-foreground" : "hover:bg-muted"
          )}
          onClick={() => setState((s) => withRowAction(s, collision, "keep_both"))}
        >
          Keep both
        </button>
        <button
          type="button"
          disabled={busy}
          className={cn(
            "flex-1 border-l px-3 py-1.5 transition-colors",
            mode === "rename" ? "bg-primary text-primary-foreground" : "hover:bg-muted"
          )}
          onClick={() => setState((s) => withRowAction(s, collision, "rename"))}
        >
          Rename
        </button>
      </div>
      {mode === "keep_both" ? (
        <p className="break-words text-[13px] text-muted-foreground">
          Will be named{" "}
          <span className="font-medium text-foreground">{collision.suggestion}</span>
        </p>
      ) : (
        <Input
          aria-label="New name"
          value={renameValue(state, collision)}
          autoFocus
          maxLength={200}
          disabled={busy}
          onChange={(e) =>
            setState((s) => setOverride(s, collision.path, "rename", e.target.value))
          }
        />
      )}
      {problem && <p className="text-[12px] text-destructive">{problem}</p>}
    </div>
  );
}

interface ConflictDialogProps {
  open: boolean;
  busy?: boolean;
  /** Defaults to the prototype's copy for the variant. */
  title?: string;
  description?: string;
  collisions: LatexCollision[];
  /** Every path (or, for `variant="name"`, every document name) in the
   * target, for the advisory check. */
  taken: string[];
  /**
   * `"files"` (default): one row per colliding path, with an "Apply to all"
   * toggle -- the prototype's file-conflict dialog. `"name"`: the single
   * taken-document-name question, answered from `collisions[0]` -- the
   * prototype's name-conflict dialog.
   */
  variant?: "files" | "name";
  onCancel: () => void;
  onConfirm: (decisions: { path: string; new_path: string }[]) => void;
}

// Rows beyond this many collapse behind "Show all N" -- a merge of a large
// archive can report dozens.
const VISIBLE_LIMIT = 5;

function filesDescription(n: number): string {
  return n === 1
    ? "1 incoming file matches a path already in this project. Keep both, or rename."
    : `${n} incoming files match paths already in this project. Keep both, or rename.`;
}

/**
 * The one conflict dialog every duplicate-name surface (upload, new file,
 * rename, create) shares. Holds exactly one `useState<ConflictState>` and
 * computes nothing itself -- every transition goes through `lib/latex-conflicts`,
 * and every displayed suggestion is the server's own, never recomputed here.
 */
export function ConflictDialog({
  open,
  busy = false,
  title,
  description,
  collisions,
  taken,
  variant = "files",
  onCancel,
  onConfirm,
}: ConflictDialogProps) {
  const [state, setState] = useState<ConflictState>(initialState());
  const [expanded, setExpanded] = useState(false);

  // Reset when the dialog actually closes -- a second conflict (a different
  // upload, a different rename) must not inherit the previous one's
  // per-row overrides.
  useEffect(() => {
    if (!open) {
      setState(initialState());
      setExpanded(false);
    }
  }, [open]);

  // The rows this dialog actually asks about: the one name, or every file.
  const asked = variant === "name" ? collisions.slice(0, 1) : collisions;
  const rowProblems = problems(state, asked, taken);
  const hasProblems = Object.keys(rowProblems).length > 0;
  // A problem on a COLLAPSED row disables Confirm with nothing on screen to
  // explain it, and no way to reach the row that caused it -- so the list
  // force-expands whenever a hidden row is the one holding Confirm down.
  const hiddenProblem = collisions
    .slice(VISIBLE_LIMIT)
    .some((c) => rowProblems[c.path] !== undefined);
  const showAll = expanded || hiddenProblem;
  const visible = showAll ? collisions : collisions.slice(0, VISIBLE_LIMIT);

  function confirm() {
    if (busy || hasProblems) return;
    onConfirm(decisions(state, asked));
  }

  const footer = (
    <DialogFooter>
      <Button variant="outline" onClick={onCancel} disabled={busy}>
        Cancel
      </Button>
      <Button disabled={busy || hasProblems} onClick={confirm}>
        {busy && <Loader2 className="animate-spin" />}
        Confirm
      </Button>
    </DialogFooter>
  );

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !busy && onCancel()}>
      {variant === "name" ? (
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{title ?? "That name is taken"}</DialogTitle>
            <DialogDescription>
              {description ??
                "This project already has a LaTeX project with that name. Keep both, or choose a different name."}
            </DialogDescription>
          </DialogHeader>
          {asked[0] && (
            <NameConflictFields
              collision={asked[0]}
              state={state}
              setState={setState}
              taken={taken}
              busy={busy}
            />
          )}
          {footer}
        </DialogContent>
      ) : (
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{title ?? "Some files already exist"}</DialogTitle>
            <DialogDescription>
              {description ?? filesDescription(collisions.length)}
            </DialogDescription>
          </DialogHeader>

          <div className="flex items-center justify-between text-[12px]">
            <span className="text-muted-foreground">Apply to all</span>
            <div className="flex overflow-hidden rounded-md border">
              {/* Every row back to the batch choice, typed names included --
                  what "all" says. Composed from the lib's own transitions:
                  a fresh state with the new default. */}
              <button
                type="button"
                disabled={busy}
                className="px-2.5 py-1 hover:bg-muted"
                onClick={() => setState(setDefault(initialState(), "keep_both"))}
              >
                Keep both
              </button>
              <button
                type="button"
                disabled={busy}
                className="border-l px-2.5 py-1 hover:bg-muted"
                onClick={() => setState(setDefault(initialState(), "rename"))}
              >
                Rename
              </button>
            </div>
          </div>

          <div className="max-h-80 overflow-y-auto">
            <FileConflictList
              collisions={collisions}
              rows={visible}
              state={state}
              setState={setState}
              taken={taken}
              busy={busy}
            />
          </div>

          {!showAll && collisions.length > VISIBLE_LIMIT && (
            <Button variant="ghost" size="sm" onClick={() => setExpanded(true)}>
              Show all {collisions.length}
            </Button>
          )}

          {footer}
        </DialogContent>
      )}
    </Dialog>
  );
}
