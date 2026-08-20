import { describe, expect, it } from "vitest";
import { firstErrorMessage } from "./latex-log";

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

describe("firstErrorMessage", () => {
  // This function produces the panel's HEADLINE ONLY. The file and the line
  // the editor jumps to come from the compile response (`error_file` /
  // `error_line`), decided by the compile service against the tree it
  // staged -- never from this text. See the module's own header for the two
  // withdrawn attempts that read them from here.

  it("takes the message after a -file-line-error prefix and DROPS the path", () => {
    expect(firstErrorMessage(SPACE_IN_PATH)).toBe("Undefined control sequence.");
  });

  it("is not confused by the stray ')' an Overfull echo puts in the log", () => {
    expect(firstErrorMessage(OVERFULL_PARENS)).toBe("Undefined control sequence.");
  });

  it("still reads the bare '! ...' form, which TeX writes with no position", () => {
    expect(firstErrorMessage(OVERFULL_PARENS_NO_FLAG)).toBe("Undefined control sequence.");
  });

  it("keeps the CAUSE for a missing package, not the Emergency stop fallout", () => {
    expect(firstErrorMessage(MISSING_PACKAGE)).toBe(
      "LaTeX Error: File `nopesuchpkg.sty' not found."
    );
  });

  it("finds nothing in a clean log, so a successful compile shows no error banner", () => {
    expect(firstErrorMessage(CLEAN)).toBeNull();
  });

  it("finds nothing in a timeout message, which is not a TeX error and is shown verbatim", () => {
    expect(firstErrorMessage("Compilation exceeded 30s and was stopped.")).toBeNull();
  });

  it("ignores a '!' that is not at the start of a line, so prose is not mistaken for an error", () => {
    expect(firstErrorMessage("Package foo warning: watch out! really\n")).toBeNull();
  });

  it("exports no way to read a file or a line out of a log", async () => {
    // A GUARD, not a formality. Both withdrawn attempts were a helper in
    // this module that returned a file name, and both were called in good
    // faith by a caller that had no way to know the answer was a guess. If
    // a future change reintroduces one, this fails and sends the reader to
    // the header above.
    const exported = await import("./latex-log");
    expect(Object.keys(exported)).toEqual(["firstErrorMessage"]);
  });
});
