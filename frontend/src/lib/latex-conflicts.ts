import type { LatexCollision } from "@/lib/latex";

/**
 * The conflict dialog's decisions, as data.
 *
 * PURE -- no React, no fetch. vitest here runs in the node environment with
 * no jsdom, so anything worth a test lives in `src/lib/`; and every silent
 * data-loss bug in the LaTeX editor has come from logic that lived as refs
 * inside a component.
 *
 * This module never computes a `(n)` name. `suggestion` comes from the
 * server (`latex_dedupe.suffix_path`), which is the single implementation of
 * that rule -- the same reason path validation lives only in
 * `latex_paths.normalize_path` and never in the browser.
 */

export type ConflictAction = "keep_both" | "rename";

export interface ConflictState {
  defaultAction: ConflictAction;
  overrides: Record<string, { action: ConflictAction; newPath: string }>;
}

export function initialState(): ConflictState {
  return { defaultAction: "keep_both", overrides: {} };
}

export function setDefault(state: ConflictState, action: ConflictAction): ConflictState {
  return { ...state, defaultAction: action };
}

export function setOverride(
  state: ConflictState,
  path: string,
  action: ConflictAction,
  newPath: string
): ConflictState {
  return { ...state, overrides: { ...state.overrides, [path]: { action, newPath } } };
}

export function clearOverride(state: ConflictState, path: string): ConflictState {
  const overrides = { ...state.overrides };
  delete overrides[path];
  return { ...state, overrides };
}

export function resolvedPath(state: ConflictState, collision: LatexCollision): string {
  const override = state.overrides[collision.path];
  if (override?.action === "rename") return override.newPath.trim();
  return collision.suggestion;
}

export function decisions(
  state: ConflictState,
  collisions: LatexCollision[]
): { path: string; new_path: string }[] {
  return collisions.map((c) => ({ path: c.path, new_path: resolvedPath(state, c) }));
}

/** Mirrors the backend's own fold (`latex_paths.collision_key`). */
function key(path: string): string {
  return path.toLowerCase();
}

export function problems(
  state: ConflictState,
  collisions: LatexCollision[],
  taken: string[]
): Record<string, string> {
  const found: Record<string, string> = {};
  const seen = new Map<string, string>();
  for (const t of taken) seen.set(key(t), t);

  for (const c of collisions) {
    const resolved = resolvedPath(state, c);
    if (!resolved) {
      found[c.path] = "Enter a name.";
      continue;
    }
    const k = key(resolved);
    // The path this row is REPLACING is not a conflict with itself: the
    // original is exactly what is being kept.
    if (seen.has(k) && key(c.existing) !== k) {
      found[c.path] = `${seen.get(k)} is already taken.`;
      continue;
    }
    seen.set(k, resolved);
  }
  return found;
}
