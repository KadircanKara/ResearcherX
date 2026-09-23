import { describe, expect, it } from "vitest";
import {
  LANDING_THEME_BOOT,
  LANDING_THEME_KEY,
  otherLandingTheme,
  parseLandingTheme,
  resolveLandingTheme,
} from "./landing-theme";

describe("landing theme", () => {
  it("accepts only light or dark as a stored choice", () => {
    expect(parseLandingTheme("light")).toBe("light");
    expect(parseLandingTheme("dark")).toBe("dark");
    expect(parseLandingTheme("system")).toBeNull();
    expect(parseLandingTheme(null)).toBeNull();
  });

  it("lets an explicit choice win over the system", () => {
    expect(resolveLandingTheme("dark", true)).toBe("dark");
    expect(resolveLandingTheme("light", false)).toBe("light");
  });

  it("follows the system when nothing was chosen, dark until it is known", () => {
    expect(resolveLandingTheme(null, true)).toBe("light");
    expect(resolveLandingTheme(null, false)).toBe("dark");
    expect(resolveLandingTheme(null, null)).toBe("dark");
  });

  it("flips between the two themes", () => {
    expect(otherLandingTheme("light")).toBe("dark");
    expect(otherLandingTheme("dark")).toBe("light");
  });

  it("boots from the same storage key it is saved under", () => {
    expect(LANDING_THEME_BOOT).toContain(JSON.stringify(LANDING_THEME_KEY));
  });
});
