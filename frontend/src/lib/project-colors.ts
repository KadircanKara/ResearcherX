/**
 * The project colour palette, mirrored from `backend/app/core/palette.py`.
 *
 * A MIRROR, and deliberately not the authority: the server validates every
 * colour it stores against its own copy, so a value this file allows but the
 * server does not is a 422, never a stored colour outside the allowlist. What
 * this copy is for is rendering the swatch picker without a round trip.
 *
 * Kept pure (no React, no fetch) because vitest here runs in the node
 * environment with no jsdom -- a rule worth testing has to live in `lib/`.
 */

export const PROJECT_COLORS = [
  "#3B82F6", // blue
  "#8B5CF6", // violet
  "#EC4899", // pink
  "#EF4444", // red
  "#F97316", // orange
  "#EAB308", // amber
  "#22C55E", // green
  "#14B8A6", // teal
  "#06B6D4", // cyan
  "#64748B", // slate
] as const;

export type ProjectColor = (typeof PROJECT_COLORS)[number];

export function isProjectColor(value: string): value is ProjectColor {
  return (PROJECT_COLORS as readonly string[]).includes(value);
}

/**
 * The colour to actually paint for a project.
 *
 * The server always sends one, so this is the guard for the two cases where it
 * cannot be trusted: a response cached by a browser from before the field
 * existed, and a value that is not in this build's palette (the server's copy
 * moved ahead of the client's). Both fall back to a stable derivation rather
 * than rendering an unknown string into a `style` attribute.
 */
export function colorFor(project: { id: string; color?: string | null }): ProjectColor {
  if (project.color && isProjectColor(project.color)) return project.color;
  let total = 0;
  for (const ch of project.id) total += ch.charCodeAt(0);
  return PROJECT_COLORS[total % PROJECT_COLORS.length];
}
