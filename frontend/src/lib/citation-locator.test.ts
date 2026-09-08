import { describe, expect, it } from "vitest";
import { formatChunkLocator } from "./citation-locator";

describe("formatChunkLocator", () => {
  it("joins the section path with the backend's own separator", () => {
    expect(formatChunkLocator(["IV. RL", "B. Reward"], 3)).toBe(
      "IV. RL > B. Reward · p. 3"
    );
  });

  it("renders a section with no page, and a page with no section", () => {
    expect(formatChunkLocator(["Introduction"], null)).toBe("Introduction");
    expect(formatChunkLocator([], 7)).toBe("p. 7");
  });

  it("returns nothing for a chunk indexed before structured chunking", () => {
    // section: [], page: null is EVERY row until the re-index runs. The card
    // must look exactly as it does today for these — no separator, no label.
    expect(formatChunkLocator([], null)).toBe("");
    expect(formatChunkLocator()).toBe("");
    expect(formatChunkLocator(undefined, undefined)).toBe("");
  });

  it("drops empty path elements instead of joining around them", () => {
    expect(formatChunkLocator(["IV. RL", "", "B. Reward"], null)).toBe(
      "IV. RL > B. Reward"
    );
    expect(formatChunkLocator(["   "], null)).toBe("");
  });

  it("keeps page 0 rather than treating it as absent", () => {
    expect(formatChunkLocator([], 0)).toBe("p. 0");
  });
});
