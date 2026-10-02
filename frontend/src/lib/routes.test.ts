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
      routes.latex("p"),
      routes.latexDoc("p", "d"),
    ];
    for (const path of all) expect(path.startsWith(ADMIN_BASE)).toBe(true);
  });

  it("nests project pages under the project, so startsWith checks stay valid", () => {
    const project = routes.project("p1");
    expect(routes.chat("p1").startsWith(project + "/")).toBe(true);
    expect(routes.conversation("p1", "c1")).toBe(`${routes.chat("p1")}/c1`);
    expect(routes.latexDoc("p1", "d1")).toBe(`${routes.latex("p1")}/d1`);
  });

  it("login lives outside the app prefix", () => {
    expect(routes.login()).toBe("/login");
  });

  it("leaves the root for the landing page", () => {
    expect(ADMIN_BASE).not.toBe("/");
    expect(routes.home()).toBe("/admin");
  });
});

describe("projectTab", () => {
  it("reads the tab from a project path", () => {
    expect(projectTab(routes.papers("p1"), "p1")).toBe("papers");
    expect(projectTab(routes.conversation("p1", "c1"), "p1")).toBe("chat");
    expect(projectTab(routes.latexDoc("p1", "d1"), "p1")).toBe("latex");
  });

  it("falls back to chat for the bare project path", () => {
    expect(projectTab(routes.project("p1"), "p1")).toBe("chat");
    expect(projectTab("/admin/research/p1/graph", "p1")).toBe("chat"); // no such tab
  });

  it("matches whole segments, not prefixes", () => {
    expect(projectTab(`${routes.papers("p1")}-archive`, "p1")).toBe("chat");
  });
});
