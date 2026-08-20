import { describe, expect, it } from "vitest";
import { firstError } from "./latex-log";

const UNDEFINED_CS = `
This is pdfTeX, Version 3.141592653-2.6-1.40.26
(./p.tex
LaTeX2e <2024-11-01>
! Undefined control sequence.
l.7 \\bogus

?
! Emergency stop.
`;

describe("firstError", () => {
  it("returns the first error and the line it points at", () => {
    expect(firstError(UNDEFINED_CS)).toEqual({
      message: "Undefined control sequence.",
      line: 7,
      file: "p.tex",
    });
  });

  it("returns the FIRST error, not the last, because later ones are usually fallout", () => {
    const log = "! Missing $ inserted.\nl.3 x\n! Emergency stop.\nl.99 y\n";
    expect(firstError(log)).toEqual({
      message: "Missing $ inserted.",
      line: 3,
      file: null,
    });
  });

  it("returns a null line when no l.<n> follows, rather than inventing one", () => {
    expect(firstError("! LaTeX Error: File `nope.sty' not found.\n")).toEqual({
      message: "LaTeX Error: File `nope.sty' not found.",
      line: null,
      file: null,
    });
  });

  it("finds nothing in a clean log, so a successful compile shows no error banner", () => {
    expect(firstError("Output written on p.pdf (1 page, 9135 bytes).\n")).toBeNull();
  });

  it("finds nothing in a timeout message, which is not a TeX error and is shown verbatim", () => {
    expect(firstError("Compilation exceeded 30s and was stopped.")).toBeNull();
  });

  it("ignores a '!' that is not at the start of a line, so prose in the log is not mistaken for an error", () => {
    expect(firstError("Package foo warning: watch out! really\n")).toBeNull();
  });
});

// `l.<n>` is relative to whichever file TeX was reading, which in a
// multi-file project is usually a chapter and often one that is not even
// open. Every case where the stack cannot be read with certainty answers
// `file: null`, and the shell then declines to jump.
describe("firstError file attribution", () => {
  it("blames the INNERMOST open file, not the main file", () => {
    const log = "(./main.tex\n(./chapters/intro.tex\n! Undefined control sequence.\nl.4 \\x\n";
    expect(firstError(log)?.file).toBe("chapters/intro.tex");
  });

  it("returns to the enclosing file once the inner one is closed", () => {
    const log = "(./main.tex (./chapters/intro.tex)\n! Missing $ inserted.\nl.9 x\n";
    expect(firstError(log)?.file).toBe("main.tex");
  });

  it("handles an open and close on the same line as the error's predecessor", () => {
    const log = "(./main.tex\n(./a.tex) (./b.tex)\n! Bad.\nl.1 x\n";
    expect(firstError(log)?.file).toBe("main.tex");
  });

  it("reads a quoted path, so a filename with spaces is not cut at the space", () => {
    const log = '(./main.tex\n("./my chapter.tex"\n! Bad.\nl.2 x\n';
    expect(firstError(log)?.file).toBe("my chapter.tex");
  });

  it("names no file for an unterminated quoted path rather than a truncated one", () => {
    const log = '(./main.tex\n("./my chapter\n! Bad.\nl.2 x\n';
    expect(firstError(log)?.file).toBeNull();
  });

  it("names no file when a ')' closes something that was never opened", () => {
    // The stack has lost its place; every frame below is suspect, so nothing
    // is reported rather than the wrong thing.
    const log = "(./main.tex\n(./a.tex)))\n(./b.tex\n! Bad.\nl.3 x\n";
    expect(firstError(log)?.file).toBeNull();
  });

  it("names no file for an error raised before any file was entered", () => {
    expect(firstError("! I can't find file `x'.\nl.1 x\n")?.file).toBeNull();
  });

  it("names no file when the innermost frame is not a file at all", () => {
    // `(\end occurred inside a group` opens a frame that is not a path. The
    // enclosing main.tex is NOT reached past to -- "probably main.tex" is
    // exactly the guess that produces a confident wrong jump.
    const log = "(./main.tex\n(\\end occurred inside a group\n! Bad.\nl.5 x\n";
    expect(firstError(log)?.file).toBeNull();
  });

  it("does not mistake page markers and font groups for files", () => {
    const log = "(./main.tex\n[1] [2] (Font) \\OT1/cmr/m/n/10\n! Bad.\nl.6 x\n";
    expect(firstError(log)?.file).toBe("main.tex");
  });

  it("does not let parens inside the error's own message change the answer", () => {
    const log = "(./main.tex\n! Package foo Error: unbalanced ) here.\nl.6 x\n";
    expect(firstError(log)).toEqual({
      message: "Package foo Error: unbalanced ) here.",
      line: 6,
      file: "main.tex",
    });
  });
});
