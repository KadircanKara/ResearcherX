import { describe, expect, it } from "vitest";
import { heroPoster, heroVideoSrc, heroVideoTier } from "./landing-video";

describe("heroVideoTier", () => {
  it("serves phones the smallest file", () => {
    expect(heroVideoTier(360)).toBe("sm");
    expect(heroVideoTier(767)).toBe("sm");
  });

  it("defaults to the middle file on ordinary screens", () => {
    expect(heroVideoTier(768)).toBe("md");
    expect(heroVideoTier(1440)).toBe("md");
    expect(heroVideoTier(1599)).toBe("md");
  });

  it("spends the largest file only on wide displays", () => {
    expect(heroVideoTier(1600)).toBe("xl");
    expect(heroVideoTier(2560)).toBe("xl");
  });

  it("names a distinct file for every theme and tier", () => {
    const urls = (["dark", "light"] as const).flatMap((theme) =>
      (["sm", "md", "xl"] as const).map((tier) => heroVideoSrc(theme, tier)),
    );
    expect(new Set(urls).size).toBe(6);
    expect(heroVideoSrc("light", "xl")).toBe("/landing/hero-light-xl.mp4");
    expect(heroPoster("dark", "md")).toBe("/landing/hero-dark-poster.jpg");
    expect(heroPoster("light", "sm")).toBe("/landing/hero-light-sm-poster.jpg");
  });
});
