import { describe, expect, it } from "vitest";
import { matchesQuery } from "./search";

describe("matchesQuery", () => {
  it("matches everything when the query is empty or blank", () => {
    // The box is a filter, and an empty filter removes nothing.
    expect(matchesQuery("", ["anything"])).toBe(true);
    expect(matchesQuery("   ", ["anything"])).toBe(true);
  });

  it("is case-insensitive in both directions", () => {
    expect(matchesQuery("UAV", ["multi-uav path planning"])).toBe(true);
    expect(matchesQuery("uav", ["Multi-UAV Path Planning"])).toBe(true);
  });

  it("matches inside a word, so hyphenated and joined titles are findable", () => {
    // A word-boundary rule would miss exactly the titles this domain is
    // full of.
    expect(matchesQuery("uav", ["Multi-UAV Swarms"])).toBe(true);
  });

  it("requires EVERY term, so typing more narrows", () => {
    expect(matchesQuery("uav coverage", ["UAV coverage and connectivity"])).toBe(true);
    expect(matchesQuery("uav satellite", ["UAV coverage and connectivity"])).toBe(false);
  });

  it("matches terms in any order", () => {
    expect(matchesQuery("coverage uav", ["UAV coverage"])).toBe(true);
  });

  it("searches across every field it is given", () => {
    expect(matchesQuery("swarm", ["A title", "an abstract about swarm robotics"])).toBe(true);
  });

  it("never matches by spanning the seam between two fields", () => {
    // "title" ends field one and "abstract" starts field two; a naive join
    // would let a term straddle them and appear to find text nobody wrote.
    expect(matchesQuery("titleabstract", ["title", "abstract"])).toBe(false);
  });

  it("ignores null and undefined fields rather than matching on them", () => {
    expect(matchesQuery("null", ["a title", null, undefined])).toBe(false);
    expect(matchesQuery("title", ["a title", null])).toBe(true);
  });

  it("collapses repeated whitespace between terms", () => {
    expect(matchesQuery("  uav   coverage ", ["UAV coverage"])).toBe(true);
  });

  it("does not match when there are no fields at all", () => {
    expect(matchesQuery("anything", [])).toBe(false);
  });
});
