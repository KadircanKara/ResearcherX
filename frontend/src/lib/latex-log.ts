export interface LatexError {
  message: string;
  /** The source line TeX blamed, when the log names one. */
  line: number | null;
  /**
   * The file TeX was reading when it blamed that line, when the log makes it
   * UNAMBIGUOUS -- `null` whenever it does not.
   *
   * In the multi-file era `l.<n>` is relative to whichever file TeX had open,
   * usually a chapter and often one that is not even on screen, so a caller
   * that jumps to that line of the active buffer is confidently wrong. `null`
   * means "could not tell": the caller must decline to jump and say so, on
   * the same line this codebase holds elsewhere -- a confident wrong answer
   * is acted on, an honest question is answered.
   */
  file: string | null;
}

// TeX writes an error as a line beginning "! ", optionally followed a line or
// two later by "l.<n> <the offending source>". Anchoring on the start of a
// line matters: logs are full of prose containing exclamation marks, and a
// loose search reports package chatter as a compile error.
const ERROR_LINE = /^!\s*(.+?)\s*$/;
const SOURCE_LINE = /^l\.(\d+)\s/;

/**
 * What counts as a FILE name in `(<token>`.
 *
 * TeX opens a file by printing `(` immediately followed by its path, but it
 * also prints `(` for things that are not files at all -- `(Font)`,
 * `(\end occurred inside a group)`, `(see the transcript file...)`. Requiring
 * a trailing extension is what separates them. A log line wrapped at 79
 * columns can also split a long path in two, and the truncated head almost
 * never ends in an extension, so it lands in the not-a-file bucket rather
 * than being reported as a real file -- which is the direction to be wrong
 * in.
 */
const FILE_TOKEN = /^[^\s()"]*\.[A-Za-z0-9_-]+$/;

/**
 * The same test for a QUOTED path, where a space is part of the name rather
 * than the token's terminator. Kept separate rather than dropping `\s` from
 * the rule above: unquoted, a space genuinely does end the path, and
 * accepting one there would swallow the prose that follows a `(` group.
 */
const QUOTED_FILE_TOKEN = /^[^()"]*\.[A-Za-z0-9_-]+$/;

interface FileStack {
  /** One entry per unclosed `(`. `null` = opened something that is not a file. */
  entries: (string | null)[];
  /**
   * Set the moment the parens stop making sense -- a `)` with nothing open,
   * or a quoted path with no closing quote. From then on NO file is
   * reported: a stack that has lost its place cannot be trusted to name the
   * innermost file, and naming the wrong one is the failure this whole
   * mechanism exists to avoid.
   */
  broken: boolean;
}

/** Paths are printed relative to the compile root, with a `./` TeX adds. */
function normalize(name: string): string {
  return name.replace(/^\.\//, "");
}

/** Applies one log line's parens to the stack. */
function scan(line: string, stack: FileStack): void {
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === "(") {
      let name: string;
      let quoted = false;
      if (line[i + 1] === '"') {
        quoted = true;
        // A path containing spaces is QUOTED by TeX, so the space is not a
        // terminator here -- the closing quote is. Without this branch a
        // `("./my chapter.tex"` opens a frame named `./my` and every error
        // under it is attributed to a file that does not exist.
        const close = line.indexOf('"', i + 2);
        if (close === -1) {
          stack.broken = true;
          return;
        }
        name = line.slice(i + 2, close);
        i = close;
      } else {
        let end = i + 1;
        while (end < line.length && !" \t()".includes(line[end])) end += 1;
        name = line.slice(i + 1, end);
        i = end - 1;
      }
      const looksLikeFile = quoted ? QUOTED_FILE_TOKEN.test(name) : FILE_TOKEN.test(name);
      stack.entries.push(looksLikeFile ? normalize(name) : null);
    } else if (ch === ")") {
      if (stack.entries.length === 0) {
        stack.broken = true;
        return;
      }
      stack.entries.pop();
    }
  }
}

/**
 * The innermost open file, or null.
 *
 * If the innermost frame is not a file (a `(Font)`-style group), this answers
 * null rather than reaching past it to the enclosing file: TeX may well have
 * been reading that enclosing file, but "may well have been" is exactly the
 * guess that produces a confident wrong jump.
 */
function currentFile(stack: FileStack): string | null {
  if (stack.broken || stack.entries.length === 0) return null;
  return stack.entries[stack.entries.length - 1];
}

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
  // The file stack as of the START of the line being examined. Lines are fed
  // to `scan` only AFTER they have been ruled out as the error line: an
  // error's own message text can contain unbalanced parens
  // (`! Package foo Error: bad )`), and letting that corrupt the stack would
  // change the answer for the very line being reported on.
  const stack: FileStack = { entries: [], broken: false };
  for (let i = 0; i < lines.length; i++) {
    const hit = ERROR_LINE.exec(lines[i]);
    if (!hit) {
      scan(lines[i], stack);
      continue;
    }
    const file = currentFile(stack);
    // Look a short way ahead for the "l.<n>" that names the source line. TeX
    // puts it immediately after in practice; the small window keeps a later,
    // unrelated error's line number from being attached to this one.
    for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
      const at = SOURCE_LINE.exec(lines[j]);
      if (at) return { message: hit[1], line: Number(at[1]), file };
      if (ERROR_LINE.test(lines[j])) break;
    }
    return { message: hit[1], line: null, file };
  }
  return null;
}
