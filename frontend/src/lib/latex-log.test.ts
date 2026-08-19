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
    });
  });

  it("returns the FIRST error, not the last, because later ones are usually fallout", () => {
    const log = "! Missing $ inserted.\nl.3 x\n! Emergency stop.\nl.99 y\n";
    expect(firstError(log)).toEqual({ message: "Missing $ inserted.", line: 3 });
  });

  it("returns a null line when no l.<n> follows, rather than inventing one", () => {
    expect(firstError("! LaTeX Error: File `nope.sty' not found.\n")).toEqual({
      message: "LaTeX Error: File `nope.sty' not found.",
      line: null,
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
