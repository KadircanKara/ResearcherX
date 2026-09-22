import { describe, expect, it } from "vitest";
import {
  DEFAULT_PROJECT_VIEW,
  NO_DESCRIPTION,
  filterProjects,
  parseKeywords,
  parseProjectView,
  projectBlurb,
} from "./project-view";

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

describe("filterProjects", () => {
  const projects = [
    { title: "Multi-UAV Coordination", topic_keywords: ["swarm", "coverage"] },
    { title: "Fleet Intelligence", topic_keywords: ["telemetry"] },
  ];

  it("keeps every project for a blank query", () => {
    expect(filterProjects(projects, "")).toBe(projects);
    expect(filterProjects(projects, "   ")).toBe(projects);
  });

  it("matches the title ignoring case and surrounding spaces", () => {
    expect(filterProjects(projects, "  FLEET ")).toEqual([projects[1]]);
  });

  it("matches a keyword substring", () => {
    expect(filterProjects(projects, "swa")).toEqual([projects[0]]);
  });

  it("returns nothing when neither title nor keywords match", () => {
    expect(filterProjects(projects, "lidar")).toEqual([]);
  });
});

describe("parseKeywords", () => {
  it("splits on commas, trims, and drops empties", () => {
    expect(parseKeywords("carbon, energy ,, policy ")).toEqual(["carbon", "energy", "policy"]);
  });

  it("reads a blank field as no keywords", () => {
    expect(parseKeywords("")).toEqual([]);
    expect(parseKeywords(" , ")).toEqual([]);
  });
});

describe("projectBlurb", () => {
  it("shows the description when there is one", () => {
    expect(projectBlurb({ description: "Swarm coverage." })).toBe("Swarm coverage.");
  });

  it("falls back for a null or blank description", () => {
    expect(projectBlurb({ description: null })).toBe(NO_DESCRIPTION);
    expect(projectBlurb({ description: "  " })).toBe(NO_DESCRIPTION);
  });
});
