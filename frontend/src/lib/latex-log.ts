export interface LatexError {
  message: string;
  /**
   * The source line TeX blamed, or null when the log does not state it in a
   * form that also names the file.
   *
   * `line` and `file` are set or null TOGETHER, on purpose. A line number
   * without a file is worse than no line number at all in a multi-file
   * project: the caller would jump that many lines into whatever buffer
   * happens to be on screen, which is usually not the file the error is in.
   */
  line: number | null;
  /** The file TeX blamed, tree-relative, or null. See `line`. */
  file: string | null;
}

/**
 * Errors, as `-file-line-error` writes them.
 *
 * The compiler runs `latexmk ... -file-line-error` (see
 * `latex-compiler/app.py`), which makes every TeX error self-describing:
 *
 *     ./chapters/intro.tex:3: Undefined control sequence.
 *
 * instead of a bare `! Undefined control sequence.` whose file had to be
 * INFERRED from somewhere else in the log.
 *
 * This replaced a parser that tracked TeX's file stack by counting `(` and
 * `)`. That parser was disproved against the real compiler in this project's
 * own container: TeX echoes the offending typeset text inside `Overfull
 * \hbox` warnings, and that echo carries literal parentheses straight out of
 * the user's source. One stray `)` in a long line pops a real file frame and
 * the parser then names the ENCLOSING file -- confidently, with a line
 * number belonging to a different file -- and the shell happily opens it and
 * jumps. Two stray parens instead emptied the stack and it answered null, so
 * the failure was INTERMITTENT, which is worse than consistent. Reproduced
 * verbatim with a stock `article` and no packages; the fixtures in
 * `latex-log.test.ts` are that real log.
 *
 * There is no fallback to the old machinery, deliberately. A fallback that
 * can produce a confident WRONG answer is exactly the thing being removed --
 * a wrong jump is worse than no jump, which is the same line
 * `paper_resolver.py` and `latex_detect.py` hold on the backend.
 *
 * The path may contain spaces (measured: `./my chapter/deep.tex:2: ...`, no
 * quoting) so a space cannot terminate it. It may NOT contain a colon -- a
 * colon is what separates the three fields, and a path holding one simply
 * fails to match and yields no file, which is the safe direction. Requiring
 * a trailing extension and forbidding a backslash is what keeps a wrapped
 * line of echoed font/typeset text (`\OT1/cmr/m/n/10 ...`) from ever being
 * read as a path.
 */
const FILE_LINE_ERROR = /^(?!!)([^\\:]*\.[A-Za-z0-9_-]+):(\d+):\s*(.+?)\s*$/;

/**
 * The legacy, file-less error line: `! <message>`.
 *
 * Still emitted even with `-file-line-error` on, for errors TeX raises when
 * it is not positioned in a file it can name -- measured: a missing package
 * prints `! LaTeX Error: File \`nopesuchpkg.sty' not found.` with no path
 * prefix, and only the FALLOUT (`./main.tex:3: Emergency stop.`) carries
 * one. Matching this form keeps the useful message; it names no file and no
 * line, so the shell declines to jump rather than jumping to the fallout.
 *
 * Anchoring on the start of a line matters: logs are full of prose
 * containing exclamation marks, and a loose search reports package chatter
 * as a compile error.
 */
const BANG_ERROR = /^!\s*(.+?)\s*$/;

/** Paths are printed relative to the compile root, with a `./` TeX adds. */
function normalize(name: string): string {
  return name.replace(/^\.\//, "");
}

/**
 * The first error in a LaTeX log, or null if there is none.
 *
 * The FIRST is what matters: TeX keeps going after an error in nonstop mode,
 * and everything after the first is usually fallout from it. With
 * `-file-line-error` even `==> Fatal error occurred` gets a path prefix, so
 * the log holds several matching lines and only the earliest is the cause.
 *
 * A log with no error line -- a compile timeout, or the generic message from
 * an unreachable compiler -- yields null, and the caller shows the log
 * verbatim.
 */
export function firstError(log: string): LatexError | null {
  for (const line of log.split("\n")) {
    const located = FILE_LINE_ERROR.exec(line);
    if (located) {
      return { message: located[3], line: Number(located[2]), file: normalize(located[1]) };
    }
    // Checked second, and the two forms are made mutually exclusive by the
    // `(?!!)` above: a `! ...` line is never read as a path, so a message
    // like "! Foo Error: see bar.tex:12: below" cannot be mined for a file
    // it does not actually name.
    const bang = BANG_ERROR.exec(line);
    if (bang) return { message: bang[1], line: null, file: null };
  }
  return null;
}
