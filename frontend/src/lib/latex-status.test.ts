import { describe, expect, it } from "vitest";
import { compileMeta, formatClock } from "./latex-status";

const AT = Date.UTC(2026, 7, 20, 14, 32, 0);

describe("formatClock", () => {
  it("is a clock time, not an elapsed figure that would need a ticking timer", () => {
    expect(formatClock(AT, "UTC")).toBe("14:32");
  });
});

describe("compileMeta", () => {
  it("names the engine and when the PDF on screen was built", () => {
    expect(compileMeta({ engine: "pdflatex", compiledAt: AT, stale: false, compiling: false }, "UTC"))
      .toEqual({ primary: "pdflatex · compiled 14:32", secondary: "Up to date" });
  });

  it("says a built PDF no longer matches the project", () => {
    expect(
      compileMeta({ engine: "xelatex", compiledAt: AT, stale: true, compiling: false }, "UTC")
        .secondary
    ).toBe("Changed since — compile to sync");
  });

  it("does not call a document that was never built out of date", () => {
    // `isStale` answers true with no compile at all, correctly — there is
    // nothing to compare. There is also no PDF, so there is nothing to say.
    expect(compileMeta({ engine: "pdflatex", compiledAt: null, stale: true, compiling: false }))
      .toEqual({ primary: "pdflatex · not compiled yet", secondary: null });
  });

  it("reports a compile in flight over everything else", () => {
    expect(
      compileMeta({ engine: "pdflatex", compiledAt: null, stale: true, compiling: true }).secondary
    ).toBe("Compiling…");
  });
});
