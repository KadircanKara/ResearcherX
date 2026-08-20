export interface TexPoint {
  x: number;
  y: number;
}

/** What was actually compiled, as opposed to what is in the editor now. */
export interface CompiledState {
  /** The document revision the backend reported for this build. */
  revision: number;
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
 * Does the PDF on screen still match the project?
 *
 * Two terms, and both are needed:
 *
 * - `dirty` is true from the KEYSTROKE, before the 800ms autosave has told
 *   the server anything. Without it the badge would appear 800ms late.
 * - `revision` is the backend's own counter, bumped by every file write,
 *   delete, rename, engine change and main_path change. It is a number the
 *   backend HANDED US, never a hash recomputed here: the backend's cache key
 *   is `tree_hash` over every file in the tree, and a second implementation
 *   of that in the browser would be free to drift on any change to it and
 *   buys nothing.
 *
 * The old rule compared the main file's source TEXT, which is now wrong for
 * a reason no comparison of one buffer can fix: editing a chapter changes
 * what compiles while leaving the main file's bytes identical.
 */
export function isStale(
  dirty: boolean,
  revision: number | null,
  compiled: CompiledState | null
): boolean {
  if (compiled === null) return true;
  if (dirty) return true;
  if (revision === null) return true;
  return revision !== compiled.revision;
}
