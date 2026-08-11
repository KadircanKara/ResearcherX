import { describe, expect, it } from "vitest";
import { highlightTerms } from "./highlight-terms";
import type { Element, Root } from "hast";

function paragraph(value: string): Root {
  return {
    type: "root",
    children: [
      { type: "element", tagName: "p", properties: {}, children: [{ type: "text", value }] },
    ],
  };
}

function childrenOf(tree: Root): unknown[] {
  return (tree.children[0] as Element).children;
}

const MARK = {
  type: "element",
  tagName: "mark",
  properties: { className: ["rounded", "bg-amber-300/30", "px-0.5", "text-inherit"] },
};

describe("highlightTerms", () => {
  it("wraps a matched term and preserves the surrounding text", () => {
    const tree = paragraph("the reward function");
    highlightTerms({ terms: ["reward"] })(tree);
    expect(childrenOf(tree)).toEqual([
      { type: "text", value: "the " },
      { ...MARK, children: [{ type: "text", value: "reward" }] },
      { type: "text", value: " function" },
    ]);
  });

  it("matches case-insensitively and keeps the original casing", () => {
    const tree = paragraph("Reward and REWARD");
    highlightTerms({ terms: ["reward"] })(tree);
    expect(childrenOf(tree)).toEqual([
      { ...MARK, children: [{ type: "text", value: "Reward" }] },
      { type: "text", value: " and " },
      { ...MARK, children: [{ type: "text", value: "REWARD" }] },
    ]);
  });

  it("respects word boundaries", () => {
    // "search" must not light up inside "researcher" — the whole point of the
    // \b anchors in the pattern.
    const tree = paragraph("a researcher searching");
    highlightTerms({ terms: ["search"] })(tree);
    expect(childrenOf(tree)).toEqual([{ type: "text", value: "a researcher searching" }]);
  });

  it("prefers the longest term where two overlap", () => {
    const tree = paragraph("about connectivity here");
    highlightTerms({ terms: ["connect", "connectivity"] })(tree);
    expect(childrenOf(tree)).toEqual([
      { type: "text", value: "about " },
      { ...MARK, children: [{ type: "text", value: "connectivity" }] },
      { type: "text", value: " here" },
    ]);
  });

  it("escapes regex metacharacters in a term", () => {
    // The "." would match any character if the term were not escaped, so
    // "nodeXjs" would light up too. queryTermsFrom splits on \W+ so a term
    // like this cannot occur today — this pins the escaping regardless.
    const tree = paragraph("node.js and nodeXjs");
    highlightTerms({ terms: ["node.js"] })(tree);
    expect(childrenOf(tree)).toEqual([
      { ...MARK, children: [{ type: "text", value: "node.js" }] },
      { type: "text", value: " and nodeXjs" },
    ]);
  });

  it("leaves the tree untouched when there are no terms", () => {
    const tree = paragraph("nothing to do here");
    highlightTerms({ terms: [] })(tree);
    expect(childrenOf(tree)).toEqual([{ type: "text", value: "nothing to do here" }]);
  });

  it("leaves the tree untouched when nothing matches", () => {
    const tree = paragraph("nothing to do here");
    highlightTerms({ terms: ["reward"] })(tree);
    expect(childrenOf(tree)).toEqual([{ type: "text", value: "nothing to do here" }]);
  });

  it("highlights inside nested elements such as emphasis", () => {
    // The source corpus is extracted as one line per chunk with no
    // newlines, so block constructs like tables never actually parse here —
    // only inline ones do. <em>/<strong> are exactly that, and are common in
    // these papers, so they're what genuinely exercises recursion into a
    // nested element; a term inside one must still highlight.
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "em",
          properties: {},
          children: [{ type: "text", value: "reward" }],
        },
      ],
    };
    highlightTerms({ terms: ["reward"] })(tree);
    expect(childrenOf(tree)).toEqual([{ ...MARK, children: [{ type: "text", value: "reward" }] }]);
  });
});
