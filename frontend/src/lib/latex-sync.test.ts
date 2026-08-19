import { describe, expect, it } from "vitest";
import { canvasToTex, isStale, texToCanvas } from "./latex-sync";

describe("coordinate conversion", () => {
  it("is the identity at scale 1, because SyncTeX big points ARE PDF.js units at scale 1", () => {
    expect(texToCanvas({ x: 169.69, y: 134.76 }, 1)).toEqual({ x: 169.69, y: 134.76 });
  });

  it("multiplies by the render scale going out to the canvas", () => {
    expect(texToCanvas({ x: 100, y: 50 }, 1.5)).toEqual({ x: 150, y: 75 });
  });

  it("divides by the render scale coming back from a click", () => {
    expect(canvasToTex({ x: 150, y: 75 }, 1.5)).toEqual({ x: 100, y: 50 });
  });

  it("round-trips at an awkward scale, so a zoomed click lands where it was aimed", () => {
    const original = { x: 233.4, y: 611.9 };
    const back = canvasToTex(texToCanvas(original, 1.33), 1.33);
    expect(back.x).toBeCloseTo(original.x, 6);
    expect(back.y).toBeCloseTo(original.y, 6);
  });
});

describe("isStale", () => {
  it("is stale before anything has been compiled", () => {
    expect(isStale("\\documentclass{article}", "pdflatex", null)).toBe(true);
  });

  it("is fresh when the buffer is byte-identical to what was compiled", () => {
    const compiled = { source: "a", engine: "pdflatex" as const, hash: "h" };
    expect(isStale("a", "pdflatex", compiled)).toBe(false);
  });

  it("is stale after a single character changes, because line numbers may have moved", () => {
    const compiled = { source: "a", engine: "pdflatex" as const, hash: "h" };
    expect(isStale("ab", "pdflatex", compiled)).toBe(true);
  });

  it("is stale when only the engine changed, since the same source lays out differently", () => {
    const compiled = { source: "a", engine: "pdflatex" as const, hash: "h" };
    expect(isStale("a", "xelatex", compiled)).toBe(true);
  });
});
