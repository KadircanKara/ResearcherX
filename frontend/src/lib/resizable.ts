/**
 * The arithmetic behind `components/ui/resizable.tsx`: panel sizes are
 * percentages of the group, and dragging a handle moves ONE boundary.
 *
 * Pure (no React, no DOM) so it is testable under vitest's node environment;
 * the component only turns pointer and key events into a delta.
 */

export interface PanelConstraint {
  /** Smallest size, in percent of the group. */
  min: number;
  /** Largest size, in percent of the group. */
  max: number;
}

/**
 * Starting sizes from each panel's `defaultSize`. A panel without one takes an
 * equal share of whatever the others left, so `[20, undefined]` is `[20, 80]`
 * and three bare panels are a third each.
 */
export function initialSizes(defaults: readonly (number | undefined)[]): number[] {
  const given = defaults.reduce<number>((sum, d) => sum + (d ?? 0), 0);
  const open = defaults.filter((d) => d === undefined).length;
  const share = open > 0 ? Math.max(0, 100 - given) / open : 0;
  return defaults.map((d) => d ?? share);
}

/**
 * Moves the boundary between panel `index` and panel `index + 1` by `delta`
 * percent (positive grows the first). The pair's combined size never changes
 * and neither panel leaves its own [min, max]; every other panel is untouched.
 * An out-of-range `index` returns the sizes unchanged.
 */
export function resizeAt(
  sizes: readonly number[],
  index: number,
  delta: number,
  constraints: readonly PanelConstraint[],
): number[] {
  if (index < 0 || index + 1 >= sizes.length) return [...sizes];
  const a = sizes[index];
  const b = sizes[index + 1];
  const total = a + b;
  const ca = constraints[index] ?? { min: 0, max: 100 };
  const cb = constraints[index + 1] ?? { min: 0, max: 100 };
  const lo = Math.max(ca.min, total - cb.max);
  const hi = Math.min(ca.max, total - cb.min);
  // Contradictory constraints (lo > hi) leave the pair where it was rather
  // than jumping to one bound.
  if (lo > hi) return [...sizes];
  const nextA = Math.min(hi, Math.max(lo, a + delta));
  const next = [...sizes];
  next[index] = nextA;
  next[index + 1] = total - nextA;
  return next;
}
