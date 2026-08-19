import type { LatexEngine } from "./latex";

export interface TexPoint {
  x: number;
  y: number;
}

/** What was actually compiled, as opposed to what is in the editor now. */
export interface CompiledState {
  source: string;
  engine: LatexEngine;
  hash: string;
}

// SyncTeX reports TeX big points -- 72 per inch, measured from the page's
// top-left -- and that is exactly the unit PDF.js reports at `scale: 1.0`.
// So the entire conversion is one multiply, and no backend code ever learns
// that zoom exists.
export function texToCanvas(p: TexPoint, scale: number): TexPoint {
  return { x: p.x * scale, y: p.y * scale };
}

export function canvasToTex(p: TexPoint, scale: number): TexPoint {
  return { x: p.x / scale, y: p.y / scale };
}

/**
 * Does the editor's buffer still match the PDF on screen?
 *
 * Deliberately compares the SOURCE TEXT rather than recomputing the backend's
 * sha256. The backend hashes `engine + NUL + source`; duplicating that here
 * would be a second implementation of someone else's contract, free to drift
 * on any change to it, in exchange for nothing -- we already hold the exact
 * string we sent. The engine counts too: the same source laid out by xelatex
 * puts different content on different pages, so the map is just as stale.
 */
export function isStale(
  source: string,
  engine: LatexEngine,
  compiled: CompiledState | null
): boolean {
  if (compiled === null) return true;
  return source !== compiled.source || engine !== compiled.engine;
}
