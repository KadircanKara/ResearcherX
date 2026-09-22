import { describe, expect, it } from "vitest";
import { canvasToTex, compileStatus, isStale, texToCanvas, texToPercent } from "./latex-sync";

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

describe("texToPercent", () => {
  it("states a point as a fraction of its page, independent of any render scale", () => {
    expect(texToPercent({ x: 306, y: 198 }, { width: 612, height: 792 })).toEqual({ x: 50, y: 25 });
  });

  it("agrees with the render-scale conversion at every displayed width", () => {
    const page = { width: 612, height: 792 };
    const point = { x: 72, y: 144 };
    for (const displayed of [300, 448, 768]) {
      const scale = displayed / page.width;
      const px = texToCanvas(point, scale);
      const pct = texToPercent(point, page);
      expect(pct.x).toBeCloseTo((px.x / displayed) * 100, 10);
      expect(pct.y).toBeCloseTo((px.y / (page.height * scale)) * 100, 10);
    }
  });
});

describe("compileStatus", () => {
  it("is idle before anything has been compiled", () => {
    expect(compileStatus({ compiling: false, failed: false, built: false })).toBe("idle");
  });

  it("reports a compile in flight over any earlier result", () => {
    expect(compileStatus({ compiling: true, failed: true, built: true })).toBe("compiling");
  });

  it("reports the latest failure even while an older PDF is still on screen", () => {
    expect(compileStatus({ compiling: false, failed: true, built: true })).toBe("failed");
  });

  it("reports a failure with no PDF at all", () => {
    expect(compileStatus({ compiling: false, failed: true, built: false })).toBe("failed");
  });

  it("reports success once a build landed and nothing has failed since", () => {
    expect(compileStatus({ compiling: false, failed: false, built: true })).toBe("success");
  });
});
