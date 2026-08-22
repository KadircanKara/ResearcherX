import { describe, expect, it } from "vitest";
import { DEFAULT_PROJECT_VIEW, parseProjectView } from "./project-view";

describe("parseProjectView", () => {
  it("keeps a known view", () => {
    expect(parseProjectView("list")).toBe("list");
    expect(parseProjectView("card")).toBe("card");
  });

  it("falls back for an absent or unrecognised value", () => {
    expect(parseProjectView(null)).toBe(DEFAULT_PROJECT_VIEW);
    expect(parseProjectView(undefined)).toBe(DEFAULT_PROJECT_VIEW);
    expect(parseProjectView("")).toBe(DEFAULT_PROJECT_VIEW);
    expect(parseProjectView("table")).toBe(DEFAULT_PROJECT_VIEW);
  });
});
