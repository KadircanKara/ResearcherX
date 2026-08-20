/**
 * What the LaTeX header says about the build, and where it gets to say it.
 *
 * The concept's header reads `pdflatex · last compiled 6 minutes ago`. There
 * is no compiled-at timestamp anywhere in the API — `CompiledState` carries
 * the document revision and the tree hash, and nothing else — so the time
 * here is the moment THIS BROWSER saw a compile land, and it is stated as a
 * clock time rather than as "6 minutes ago": an elapsed figure has to keep
 * ticking to stay true, and one that silently stops is worse than a fact.
 *
 * Staleness is NOT recomputed here. `isStale` in `lib/latex-sync.ts` owns
 * that rule (dirty buffers OR a revision the backend has moved past); this
 * only decides the words.
 */

export interface CompileMetaInput {
  engine: string;
  /** `Date.now()` when a compile last returned to this browser. */
  compiledAt: number | null;
  stale: boolean;
  compiling: boolean;
}

export function formatClock(ms: number, timeZone?: string): string {
  return new Date(ms).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    ...(timeZone ? { timeZone } : {}),
  });
}

export interface CompileMeta {
  /** Engine and when the PDF on screen was built. */
  primary: string;
  /** Whether it still matches the project. Null when there is nothing to say. */
  secondary: string | null;
}

export function compileMeta(input: CompileMetaInput, timeZone?: string): CompileMeta {
  const { engine, compiledAt, stale, compiling } = input;
  const primary =
    compiledAt === null
      ? `${engine} · not compiled yet`
      : `${engine} · compiled ${formatClock(compiledAt, timeZone)}`;

  if (compiling) return { primary, secondary: "Compiling…" };
  // Nothing has been built, so there is no PDF for anything to be stale
  // against — saying "out of date" there would describe a document that does
  // not exist. `isStale` is deliberately true in that case; this is where
  // that answer stops being a sentence.
  if (compiledAt === null) return { primary, secondary: null };
  return { primary, secondary: stale ? "Changed since — compile to sync" : "Up to date" };
}
