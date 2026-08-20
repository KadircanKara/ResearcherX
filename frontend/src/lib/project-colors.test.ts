import { describe, expect, it } from "vitest";
import { colorFor, isProjectColor, PROJECT_COLORS } from "./project-colors";

describe("colorFor", () => {
  it("uses the project's own colour when it is in the palette", () => {
    expect(colorFor({ id: "abc", color: "#22C55E" })).toBe("#22C55E");
  });

  it("falls back for a project with no colour at all", () => {
    // A response cached from before the field existed. The point is that
    // something paintable comes back, not which entry it is.
    const result = colorFor({ id: "abc" });
    expect(PROJECT_COLORS).toContain(result);
  });

  it("never returns a colour the client cannot vouch for", () => {
    // The whole reason the fallback exists: an unknown string must not reach
    // a `style` attribute just because the server sent it.
    const result = colorFor({ id: "abc", color: "javascript:alert(1)" });
    expect(PROJECT_COLORS).toContain(result);
  });

  it("is stable for the same id", () => {
    expect(colorFor({ id: "same-id" })).toBe(colorFor({ id: "same-id" }));
  });
});

describe("isProjectColor", () => {
  it("accepts palette entries and rejects everything else", () => {
    expect(isProjectColor("#3B82F6")).toBe(true);
    // Case matters: the server stores and compares the exact strings it
    // enumerates, so a lowercase copy is not the same value.
    expect(isProjectColor("#3b82f6")).toBe(false);
    expect(isProjectColor("red")).toBe(false);
  });
});
