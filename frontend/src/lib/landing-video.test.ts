import { describe, expect, it } from "vitest";
import { HERO_VIDEO, heroVideoTier } from "./landing-video";

describe("heroVideoTier", () => {
  it("serves phones the smallest file", () => {
    expect(heroVideoTier(360)).toBe("sm");
    expect(heroVideoTier(767)).toBe("sm");
  });

  it("defaults to the 540p file on ordinary screens", () => {
    expect(heroVideoTier(768)).toBe("md");
    expect(heroVideoTier(1440)).toBe("md");
    expect(heroVideoTier(1599)).toBe("md");
  });

  it("spends the 1080p file only on wide displays", () => {
    expect(heroVideoTier(1600)).toBe("xl");
    expect(heroVideoTier(2560)).toBe("xl");
  });

  it("names a distinct encoding of the same clip for every tier", () => {
    const urls = Object.values(HERO_VIDEO);
    expect(new Set(urls).size).toBe(3);
    for (const url of urls) expect(url).toContain("/3051492/");
  });
});
