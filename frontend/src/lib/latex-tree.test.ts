import { describe, expect, it } from "vitest";
import {
  basename,
  buildTree,
  formatBytes,
  isBeneath,
  isImagePath,
  isTexPath,
  joinPath,
  parentDir,
  siblingPath,
} from "./latex-tree";

const meta = (path: string, size = 10, binary = false) => ({
  path,
  is_binary: binary,
  size_bytes: size,
  updated_at: "2026-01-01T00:00:00Z",
});

describe("buildTree", () => {
  it("nests files under their directories", () => {
    const tree = buildTree([meta("main.tex"), meta("chapters/intro.tex")]);
    expect(tree.map((n) => n.name)).toEqual(["chapters", "main.tex"]);
    expect(tree[0].kind).toBe("dir");
    expect(tree[0].children.map((n) => n.path)).toEqual(["chapters/intro.tex"]);
  });

  it("creates intermediate directories that have no file of their own", () => {
    const tree = buildTree([meta("a/b/c/deep.tex")]);
    expect(tree[0].name).toBe("a");
    expect(tree[0].children[0].name).toBe("b");
    expect(tree[0].children[0].children[0].children[0].path).toBe("a/b/c/deep.tex");
  });

  it("sorts directories before files and each group case-insensitively", () => {
    const tree = buildTree([meta("Zeta.tex"), meta("alpha.tex"), meta("src/x.tex")]);
    expect(tree.map((n) => n.name)).toEqual(["src", "alpha.tex", "Zeta.tex"]);
  });

  it("gives a directory the summed size of everything beneath it", () => {
    const tree = buildTree([meta("d/a.tex", 3), meta("d/sub/b.tex", 4)]);
    expect(tree[0].size_bytes).toBe(7);
  });

  it("is stable for an empty tree", () => {
    expect(buildTree([])).toEqual([]);
  });
});

describe("path kinds", () => {
  it("treats .tex, .sty, .cls and .bib as editable text", () => {
    expect(isTexPath("a.tex")).toBe(true);
    expect(isTexPath("IEEEtran.cls")).toBe(true);
    expect(isTexPath("refs.bib")).toBe(true);
    expect(isTexPath("fig.png")).toBe(false);
  });

  it("recognises image extensions case-insensitively", () => {
    expect(isImagePath("fig.PNG")).toBe(true);
    expect(isImagePath("fig.jpeg")).toBe(true);
    expect(isImagePath("fig.pdf")).toBe(false); // a PDF is not an <img> source
  });
});

describe("path arithmetic", () => {
  it("parentDir of a root file is the empty string", () => {
    expect(parentDir("main.tex")).toBe("");
    expect(parentDir("chapters/intro.tex")).toBe("chapters");
  });

  it("joinPath does not emit a leading slash at the root", () => {
    expect(joinPath("", "main.tex")).toBe("main.tex");
    expect(joinPath("chapters", "intro.tex")).toBe("chapters/intro.tex");
  });
});

describe("isBeneath", () => {
  it("treats every path as beneath the root", () => {
    expect(isBeneath("main.tex", "")).toBe(true);
    expect(isBeneath("chapters/intro.tex", "")).toBe(true);
  });

  it("accepts a file beside or below the given directory", () => {
    expect(isBeneath("chapters/intro.tex", "chapters")).toBe(true);
    expect(isBeneath("chapters/sub/deep.tex", "chapters")).toBe(true);
  });

  it("rejects a sibling directory that merely shares a name prefix", () => {
    expect(isBeneath("chapters2/x.tex", "chapters")).toBe(false);
  });

  it("rejects a file above the given directory", () => {
    expect(isBeneath("figure.png", "chapters")).toBe(false);
  });
});

describe("basename", () => {
  it("returns a root file's path unchanged", () => {
    expect(basename("main.tex")).toBe("main.tex");
  });

  it("returns the last segment of a nested path", () => {
    expect(basename("chapters/intro.tex")).toBe("intro.tex");
  });

  it("handles a deeply nested path the same way", () => {
    expect(basename("a/b/c/deep.tex")).toBe("deep.tex");
  });
});

describe("formatBytes", () => {
  it("renders whole units without a pointless decimal", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(25 * 1024 * 1024)).toBe("25 MB");
  });

  it("promotes to the next unit when rounding would otherwise print 1024 of the smaller one", () => {
    expect(formatBytes(1048575)).toBe("1 MB");
    expect(formatBytes(1048570)).toBe("1 MB");
    expect(formatBytes(1073741823)).toBe("1 GB");
  });

  it("drops a trailing .0 that only rounding produced", () => {
    expect(formatBytes(1025)).toBe("1 KB");
    expect(formatBytes(10239)).toBe("10 KB");
  });
});

describe("siblingPath", () => {
  it("keeps a nested file in its own directory", () => {
    // The bug this exists to prevent: sending the bare leaf as the
    // destination moved the file to the tree root.
    expect(siblingPath("Figures/genetic_operators/ox.png", "ox2.png")).toBe(
      "Figures/genetic_operators/ox2.png"
    );
  });

  it("leaves a root file at the root", () => {
    expect(siblingPath("main.tex", "paper.tex")).toBe("paper.tex");
  });

  it("resolves a typed separator against the same parent", () => {
    // One field expresses both a rename and a shallow move, with no mode.
    expect(siblingPath("Figures/ox.png", "raw/ox.png")).toBe("Figures/raw/ox.png");
  });

  it("works for a directory path, which has no extension to reason about", () => {
    expect(siblingPath("Figures/genetic_operators", "operators")).toBe("Figures/operators");
  });
});
