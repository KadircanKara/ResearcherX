import { describe, expect, it } from "vitest";
import * as conflicts from "./latex-conflicts";

const COLLISIONS = [
  { path: "main.tex", existing: "main.tex", suggestion: "main (1).tex" },
  { path: "fig.png", existing: "fig.png", suggestion: "fig (1).png" },
];

describe("conflict decisions", () => {
  it("applies the batch default to every row", () => {
    const state = conflicts.initialState();
    expect(conflicts.decisions(state, COLLISIONS)).toEqual([
      { path: "main.tex", new_path: "main (1).tex" },
      { path: "fig.png", new_path: "fig (1).png" },
    ]);
  });

  it("lets one row override the default without touching its neighbours", () => {
    const state = conflicts.setOverride(
      conflicts.initialState(),
      "main.tex",
      "rename",
      "draft.tex"
    );
    expect(conflicts.decisions(state, COLLISIONS)).toEqual([
      { path: "main.tex", new_path: "draft.tex" },
      { path: "fig.png", new_path: "fig (1).png" },
    ]);
  });

  it("falls back to the default when an override is cleared", () => {
    let state = conflicts.setOverride(conflicts.initialState(), "main.tex", "rename", "draft.tex");
    state = conflicts.clearOverride(state, "main.tex");
    expect(conflicts.resolvedPath(state, COLLISIONS[0])).toBe("main (1).tex");
  });

  it("never recomputes a suggestion of its own", () => {
    // The suffix rule has exactly ONE implementation, server-side. A second
    // one here would drift, and a drifting suffix means two clients
    // disagreeing about which file an \\input names.
    const state = conflicts.initialState();
    expect(conflicts.resolvedPath(state, COLLISIONS[0])).toBe(COLLISIONS[0].suggestion);
  });

  it("reports an empty manual rename as a problem", () => {
    const state = conflicts.setOverride(conflicts.initialState(), "main.tex", "rename", "   ");
    expect(conflicts.problems(state, COLLISIONS, [])["main.tex"]).toMatch(/name/i);
  });

  it("reports a manual rename that still collides with the tree", () => {
    const state = conflicts.setOverride(conflicts.initialState(), "main.tex", "rename", "fig.png");
    expect(conflicts.problems(state, COLLISIONS, ["fig.png"])["main.tex"]).toMatch(/taken/i);
  });

  it("reports two manual renames that collide with each other", () => {
    let state = conflicts.setOverride(conflicts.initialState(), "main.tex", "rename", "same.tex");
    state = conflicts.setOverride(state, "fig.png", "rename", "same.tex");
    const found = conflicts.problems(state, COLLISIONS, []);
    expect(Object.keys(found)).toContain("fig.png");
  });

  it("treats a collision case-insensitively when validating a manual rename", () => {
    // The server folds case; a client that did not would let the user type a
    // name the commit then rejects, with the dialog already closed.
    const state = conflicts.setOverride(conflicts.initialState(), "main.tex", "rename", "FIG.PNG");
    expect(conflicts.problems(state, COLLISIONS, ["fig.png"])["main.tex"]).toMatch(/taken/i);
  });

  it("reports a manual rename back to the exact path it collided with", () => {
    // c.existing is the file ALREADY in the tree -- the one being kept, not
    // one this row is "replacing". Typing that exact name is the collision,
    // not an exemption from it.
    const state = conflicts.setOverride(conflicts.initialState(), "main.tex", "rename", "main.tex");
    expect(conflicts.problems(state, COLLISIONS, ["main.tex"])["main.tex"]).toMatch(/taken/i);
  });

  it("reports that same case-insensitively too", () => {
    const state = conflicts.setOverride(conflicts.initialState(), "main.tex", "rename", "MAIN.TEX");
    expect(conflicts.problems(state, COLLISIONS, ["main.tex"])["main.tex"]).toMatch(/taken/i);
  });
});
