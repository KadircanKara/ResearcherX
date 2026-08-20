import { describe, expect, it } from "vitest";
import { canvasToTex, isStale, texToCanvas } from "./latex-sync";

describe("isStale", () => {
  it("is stale before anything has been compiled", () => {
    expect(isStale(false, 3, null)).toBe(true);
  });

  it("is fresh when the compiled revision is the document's current one", () => {
    expect(isStale(false, 3, { revision: 3, hash: "h" })).toBe(false);
  });

  it("is stale on the keystroke, before the autosave has even fired", () => {
    // The revisions still agree -- the server has not been told yet. The
    // dirty flag is what makes the badge appear immediately rather than
    // 800ms later.
    expect(isStale(true, 3, { revision: 3, hash: "h" })).toBe(true);
  });

  it("is stale once a save has bumped the document's revision", () => {
    expect(isStale(false, 4, { revision: 3, hash: "h" })).toBe(true);
  });

  it("is stale when the document's revision is unknown", () => {
    expect(isStale(false, null, { revision: 3, hash: "h" })).toBe(true);
  });
});

describe("coordinates", () => {
  it("round-trips through the render scale", () => {
    expect(canvasToTex(texToCanvas({ x: 10, y: 20 }, 1.25), 1.25)).toEqual({ x: 10, y: 20 });
  });
});
