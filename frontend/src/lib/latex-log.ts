/**
 * WHAT THIS MODULE IS NOT: it is not where the editor learns which file an
 * error is in. That is decided by the compile service, against the tree it
 * actually staged, and arrives on the compile response as `error_file` /
 * `error_line` (see `analyse_log` in `latex-compiler/app.py`). Nothing here
 * is used for navigation.
 *
 * Two shipped attempts read the file out of the log TEXT and both were
 * withdrawn after producing a confident jump into the wrong file:
 *
 *   1. Counting `(` and `)` to track TeX's file stack. TeX echoes the
 *      offending typeset text inside `Overfull \hbox` warnings, so one
 *      literal `)` in the user's own prose popped a real frame and the
 *      parser named the enclosing file -- with a line number belonging to a
 *      different one. Two stray parens instead emptied the stack, so the
 *      failure was INTERMITTENT.
 *   2. Parsing the `-file-line-error` shape (`./chapters/intro.tex:3: msg`).
 *      Broken four ways, every one reproduced end to end against the real
 *      container: TeX wraps log lines at 79 columns, so a path over ~77
 *      characters is split and the CONTINUATION fragment is a suffix that
 *      matches a different real file; an `Overfull \hbox` echo's
 *      continuation lines carry ordinary prose (a `verbatim` block holding
 *      `parser.c:42: error: ...` is normal content in a research writing
 *      tool); `\typeout{./chapters/hacked.tex:42: ...}` needs no wrapping at
 *      all; and a colon in a filename matched nothing, which fell through to
 *      handing the client TeX's memory statistics.
 *
 * The lesson, and the constraint on anything added here: A TEX LOG IS NOT
 * STRUCTURED OUTPUT. The user's own source flows into it, so any rule that
 * infers structure from the text alone can be forged by the text. Do not
 * reintroduce a file or a line number here, in any form, however careful the
 * regex looks -- there is no careful regex, that was the discovery.
 *
 * What is left is display: the one-line headline shown above the log. Being
 * wrong about that shows the user a misleading sentence with the real log
 * directly underneath it. Being wrong about a FILE opens the wrong document
 * and scrolls it, which is why the two are no longer the same decision.
 */

/** The first error-looking line's message, for the panel headline only. */
export function firstErrorMessage(log: string): string | null {
  for (const line of log.split("\n")) {
    // `path:line: message`, as `-file-line-error` writes it. Everything
    // before the message is dropped: the file is not read from here, and
    // the panel prints the compiler's own `error_file` instead.
    const located = /^[^\s].*?:\d+:\s*(.+?)\s*$/.exec(line);
    if (located) return located[1];
    // The file-less form TeX still writes when it has no position to report
    // -- a missing package is the common one. Anchored at the start of the
    // line because logs are full of prose containing exclamation marks.
    const bang = /^!\s*(.+?)\s*$/.exec(line);
    if (bang) return bang[1];
  }
  return null;
}
