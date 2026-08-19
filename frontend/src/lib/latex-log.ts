export interface LatexError {
  message: string;
  /** The source line TeX blamed, when the log names one. */
  line: number | null;
}

// TeX writes an error as a line beginning "! ", optionally followed a line or
// two later by "l.<n> <the offending source>". Anchoring on the start of a
// line matters: logs are full of prose containing exclamation marks, and a
// loose search reports package chatter as a compile error.
const ERROR_LINE = /^!\s*(.+?)\s*$/;
const SOURCE_LINE = /^l\.(\d+)\s/;

/**
 * The first error in a LaTeX log, or null if there is none.
 *
 * The FIRST is what matters: TeX keeps going after an error in nonstop mode,
 * and everything after the first is usually fallout from it. "! Emergency
 * stop." is almost always the second entry and almost never the cause.
 *
 * A log with no "!" line -- a compile timeout, or the generic message from an
 * unreachable compiler -- yields null, and the caller shows the log verbatim.
 */
export function firstError(log: string): LatexError | null {
  const lines = log.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const hit = ERROR_LINE.exec(lines[i]);
    if (!hit) continue;
    // Look a short way ahead for the "l.<n>" that names the source line. TeX
    // puts it immediately after in practice; the small window keeps a later,
    // unrelated error's line number from being attached to this one.
    for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
      const at = SOURCE_LINE.exec(lines[j]);
      if (at) return { message: hit[1], line: Number(at[1]) };
      if (ERROR_LINE.test(lines[j])) break;
    }
    return { message: hit[1], line: null };
  }
  return null;
}
