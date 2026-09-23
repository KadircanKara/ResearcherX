import { describe, expect, it } from "vitest";
import {
  DEMO_ANSWER,
  DEMO_CITATIONS,
  DEMO_PAPERS,
  FOLLOWED_PAPER,
  REFUSAL,
  paperFor,
} from "./landing-demo";

describe("landing demo data", () => {
  it("resolves every citation to a paper and every answer sentence to a citation", () => {
    for (const c of DEMO_CITATIONS) expect(paperFor(c)).toBeDefined();
    for (const s of DEMO_ANSWER) {
      expect(DEMO_CITATIONS.some((c) => c.n === s.cite)).toBe(true);
    }
  });

  it("follows one paper, cited first in the answer", () => {
    expect(paperFor(DEMO_CITATIONS[0])).toBe(FOLLOWED_PAPER);
    expect(DEMO_ANSWER[0].cite).toBe(1);
  });

  it("writes locators in the app's own format", () => {
    expect(DEMO_CITATIONS[0].locator).toBe("3 Method > 3.2 Reward design · p. 5");
  });

  it("uses the chat's fixed refusal verbatim", () => {
    expect(REFUSAL).toBe("The ingested documents do not cover this.");
  });

  it("gives every paper a distinct bibliography key", () => {
    expect(new Set(DEMO_PAPERS.map((p) => p.bibkey)).size).toBe(DEMO_PAPERS.length);
  });
});
