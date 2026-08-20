import { describe, expect, it } from "vitest";
import { buildTree, formatBytes, isImagePath, isTexPath, joinPath, parentDir } from "./latex-tree";

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

describe("formatBytes", () => {
  it("renders whole units without a pointless decimal", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(25 * 1024 * 1024)).toBe("25 MB");
  });
});
