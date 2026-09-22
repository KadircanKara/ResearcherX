import { describe, expect, it } from "vitest";
import { ADMIN_BASE, projectTab, routes } from "./routes";

describe("routes", () => {
  it("puts every page under the admin base", () => {
    const all = [
      routes.home(),
      routes.research(),
      routes.project("p"),
      routes.chat("p"),
      routes.conversation("p", "c"),
      routes.papers("p"),
      routes.graph("p"),
      routes.latex("p"),
      routes.latexDoc("p", "d"),
      routes.explorer(),
      routes.exploration("e"),
    ];
    for (const path of all) expect(path.startsWith(ADMIN_BASE)).toBe(true);
  });

  it("nests project pages under the project, so startsWith checks stay valid", () => {
    const project = routes.project("p1");
    expect(routes.chat("p1").startsWith(project + "/")).toBe(true);
    expect(routes.conversation("p1", "c1")).toBe(`${routes.chat("p1")}/c1`);
    expect(routes.latexDoc("p1", "d1")).toBe(`${routes.latex("p1")}/d1`);
    expect(routes.exploration("e1")).toBe(`${routes.explorer()}/e1`);
  });

  it("leaves the root for the landing page", () => {
    expect(ADMIN_BASE).not.toBe("/");
    expect(routes.home()).toBe("/admin");
  });
});

describe("projectTab", () => {
  it("reads the tab from a project path", () => {
    expect(projectTab(routes.papers("p1"), "p1")).toBe("papers");
    expect(projectTab(routes.graph("p1"), "p1")).toBe("graph");
    expect(projectTab(routes.latexDoc("p1", "d1"), "p1")).toBe("latex");
    expect(projectTab(routes.conversation("p1", "c1"), "p1")).toBe("chat");
  });

  it("falls back to chat for the bare project path", () => {
    expect(projectTab(routes.project("p1"), "p1")).toBe("chat");
  });

  it("matches whole segments, not prefixes", () => {
    expect(projectTab(`${routes.papers("p1")}-archive`, "p1")).toBe("chat");
  });
});
