import { describe, expect, it } from "vitest";
import { firstError } from "./latex-log";

// EVERY fixture below is REAL text, captured from this project's own
// `latex-compiler` container (TeX Live 2026, latexmk 4.88) by running the
// exact argv `compile_tree` uses. The previous round's fixtures were
// hand-written and did not resemble real output in the two respects that
// mattered -- the parenthesis noise TeX puts in an `Overfull \hbox` echo,
// and the shape of a located error line -- which is why a confidently wrong
// file attribution shipped. Do not "tidy" these into synthetic prose.

// Project: `main.tex` with `\input{chapters/intro}`; `chapters/intro.tex`
// line 1 is a deliberately overfull paragraph whose text contains a literal
// `)`, and line 3 is `\bogusmacro`. Stock `article`, no packages.
//
// This is the exact log that disproved the `(`/`)` file-stack parser: the
// single unmatched `)` at the end of the first echoed line popped the
// `(./chapters/intro.tex` frame, and the parser then named `main.tex` --
// which exists in the tree, so the shell did not decline; it opened the
// wrong file and jumped.
const OVERFULL_PARENS = `LaTeX Font Info:    ... okay on input line 2.
 (./chapters/intro.tex
Overfull \\hbox (573.89165pt too wide) in paragraph at lines 1--2
[]\\OT1/cmr/m/n/10 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)
 []


Overfull \\hbox (655.00305pt too wide) in paragraph at lines 1--2
\\OT1/cmr/m/n/10 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
 []

./chapters/intro.tex:3: Undefined control sequence.
l.3 \\bogusmacro

The control sequence at the end of the top line
of your error message was never \\def'ed. If you have
misspelled it (e.g., \`\\hobx'), type \`I' and the correct
`;

// The SAME project compiled WITHOUT `-file-line-error`, i.e. what this
// parser is fed if the flag is ever dropped from `latex-compiler/app.py`.
const OVERFULL_PARENS_NO_FLAG = `LaTeX Font Info:    ... okay on input line 2.
 (./chapters/intro.tex
Overfull \\hbox (573.89165pt too wide) in paragraph at lines 1--2
[]\\OT1/cmr/m/n/10 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)
 []

! Undefined control sequence.
l.3 \\bogusmacro

`;

// `\\usepackage{nopesuchpkg}` in `main.tex`. Note the ORDER: the useful
// message has no path prefix, and the only located line is the FALLOUT.
const MISSING_PACKAGE = `)

! LaTeX Error: File \`nopesuchpkg.sty' not found.

Type X to quit or <RETURN> to proceed,
or enter new name. (Default extension: sty)

Enter file name:
./main.tex:3: Emergency stop.
<read *>

l.3 \\begin
          {document}^^M
*** (cannot \\read from terminal in nonstop modes)
`;

// `\\input{my chapter/deep}` -- TeX prints a path containing a space
// UNQUOTED in this form, so a space cannot terminate the path.
const SPACE_IN_PATH = ` (./my chapter/deep.tex
./my chapter/deep.tex:2: Undefined control sequence.
l.2 \\bogusmacro

`;

// The tail of a SUCCESSFUL compile of a document containing "(with parens)".
const CLEAN = `></usr/local/texlive/2026/texmf-dist/fonts/type1/public/amsfonts/cm/cmr10.pfb>
Output written on main.pdf (1 page, 23111 bytes).
PDF statistics:
 28 PDF objects out of 1000 (max. 8388607)
 20 compressed objects within 1 object stream
 3 named destinations out of 1000 (max. 500000)
 9 words of extra memory for PDF output out of 10000 (max. 10000000)
`;

describe("firstError", () => {
  it("reads the file and line straight out of a -file-line-error line", () => {
    expect(firstError(SPACE_IN_PATH)).toEqual({
      message: "Undefined control sequence.",
      line: 2,
      file: "my chapter/deep.tex",
    });
  });

  it("attributes the error to the chapter even though an Overfull echo carries a stray ')'", () => {
    // THE regression this whole rewrite exists for. The old file-stack
    // parser answered `main.tex` here -- a real file in the tree, so the
    // shell opened it and jumped to line 3 of the wrong document.
    expect(firstError(OVERFULL_PARENS)).toEqual({
      message: "Undefined control sequence.",
      line: 3,
      file: "chapters/intro.tex",
    });
  });

  it("names NO file when the compiler was not asked for one, rather than guessing", () => {
    // If `-file-line-error` is ever dropped, this degrades to "no jump",
    // never to "a jump into whichever file the parens happened to leave on
    // top of a stack".
    expect(firstError(OVERFULL_PARENS_NO_FLAG)).toEqual({
      message: "Undefined control sequence.",
      line: null,
      file: null,
    });
  });

  it("keeps the useful message for an error TeX raises with no file position", () => {
    // The missing-package message comes FIRST and carries no path; the only
    // located line in this log is `./main.tex:3: Emergency stop.`, which is
    // the fallout, not the cause. Reporting the cause with no jump beats
    // reporting the fallout with one.
    expect(firstError(MISSING_PACKAGE)).toEqual({
      message: "LaTeX Error: File `nopesuchpkg.sty' not found.",
      line: null,
      file: null,
    });
  });

  it("returns the FIRST error, not the last, because later ones are usually fallout", () => {
    // Both `==> Fatal error occurred` and any follow-on error carry a path
    // prefix too under `-file-line-error`, so the log holds several
    // matching lines.
    const log =
      "./chapters/intro.tex:3: Undefined control sequence.\n" +
      "./chapters/intro.tex:3:  ==> Fatal error occurred, no output PDF file produced!\n";
    expect(firstError(log)).toEqual({
      message: "Undefined control sequence.",
      line: 3,
      file: "chapters/intro.tex",
    });
  });

  it("finds nothing in a clean log, so a successful compile shows no error banner", () => {
    expect(firstError(CLEAN)).toBeNull();
  });

  it("finds nothing in a timeout message, which is not a TeX error and is shown verbatim", () => {
    expect(firstError("Compilation exceeded 30s and was stopped.")).toBeNull();
  });

  it("ignores a '!' that is not at the start of a line, so prose in the log is not mistaken for an error", () => {
    expect(firstError("Package foo warning: watch out! really\n")).toBeNull();
  });

  it("does not mine a path out of a '! ' message that merely mentions one", () => {
    // A `!` line names no file BY DEFINITION here -- TeX only writes that
    // form when it has no position to report. Letting a `foo.tex:12:`
    // inside the message text stand in for one would reintroduce exactly
    // the class of guess this module dropped.
    expect(firstError("! Package foo Error: see bar.tex:12: for details.\n")).toEqual({
      message: "Package foo Error: see bar.tex:12: for details.",
      line: null,
      file: null,
    });
  });
});
