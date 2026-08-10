import { describe, expect, it } from "vitest";
import { citationMarks, tokenizeCitations } from "./citation-marks";
import type { Root } from "hast";

const VALID = new Set([6, 7, 8]);

describe("tokenizeCitations", () => {
  it("splits a lone marker out of surrounding text", () => {
    expect(tokenizeCitations("a reward [6] applies", VALID)).toEqual([
      { kind: "text", value: "a reward " },
      { kind: "cite", n: 6, group: [6] },
      { kind: "text", value: " applies" },
    ]);
  });

  it("groups adjacent markers into one run", () => {
    // The run is what the card's arrows step through, so both markers must
    // carry the SAME group, not just their own number.
    expect(tokenizeCitations("penalty [7], [8].", VALID)).toEqual([
      { kind: "text", value: "penalty " },
      { kind: "cite", n: 7, group: [7, 8] },
      { kind: "text", value: ", " },
      { kind: "cite", n: 8, group: [7, 8] },
      { kind: "text", value: "." },
    ]);
  });

  it("does not group markers separated by words", () => {
    const got = tokenizeCitations("[6] and later [7]", VALID);
    expect(got.filter((t) => t.kind === "cite")).toEqual([
      { kind: "cite", n: 6, group: [6] },
      { kind: "cite", n: 7, group: [7] },
    ]);
  });

  it("groups three markers joined by commas and a trailing \"and\"", () => {
    // "[6], [7], and [8]" is the common citation phrasing — a reader sees
    // three side-by-side sources, not two grouped plus a stray one, so the
    // bare "and" before the last marker must still count as a joiner.
    expect(tokenizeCitations("as shown in [6], [7], and [8]", VALID)).toEqual([
      { kind: "text", value: "as shown in " },
      { kind: "cite", n: 6, group: [6, 7, 8] },
      { kind: "text", value: ", " },
      { kind: "cite", n: 7, group: [6, 7, 8] },
      { kind: "text", value: ", and " },
      { kind: "cite", n: 8, group: [6, 7, 8] },
    ]);
  });

  it("does not treat prose containing \"and\" as a joiner", () => {
    // This pins the boundary the previous case widened: "and compare with"
    // is prose between two independent claims, not a bare "and" joining two
    // markers, so it must NOT collapse into one run.
    const got = tokenizeCitations("see [6] and compare with [7]", VALID);
    expect(got.filter((t) => t.kind === "cite")).toEqual([
      { kind: "cite", n: 6, group: [6] },
      { kind: "cite", n: 7, group: [7] },
    ]);
  });

  it("leaves a number with no matching citation as plain text", () => {
    // chat_service rewrites out-of-range markers, but prose still contains
    // bracketed numbers of its own — a year, a section number.
    expect(tokenizeCitations("published [2015] earlier", VALID)).toEqual([
      { kind: "text", value: "published [2015] earlier" },
    ]);
  });

  it("leaves a bracketed non-number alone", () => {
    expect(tokenizeCitations("see [note A] for detail", VALID)).toEqual([
      { kind: "text", value: "see [note A] for detail" },
    ]);
  });

  it("preserves text exactly when there is nothing to do", () => {
    expect(tokenizeCitations("no markers here", VALID)).toEqual([
      { kind: "text", value: "no markers here" },
    ]);
  });
});

describe("citationMarks", () => {
  function run(tree: Root) {
    citationMarks({ valid: VALID })(tree);
    return tree;
  }

  it("replaces a marker in a paragraph with a span carrying n and group", () => {
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "p",
          properties: {},
          children: [{ type: "text", value: "reward [6] here" }],
        },
      ],
    };
    const p = run(tree).children[0] as never as { children: unknown[] };
    expect(p.children).toEqual([
      { type: "text", value: "reward " },
      {
        type: "element",
        tagName: "span",
        properties: { dataCitationN: "6", dataCitationGroup: "6" },
        children: [{ type: "text", value: "[6]" }],
      },
      { type: "text", value: " here" },
    ]);
  });

  it("leaves markers inside a code element untouched", () => {
    // The chat system prompt asks for backticks around identifiers, so
    // `arr[6]` reaches the renderer routinely. Turning an array index into a
    // citation would be a visible corruption of the answer.
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "code",
          properties: {},
          children: [{ type: "text", value: "arr[6]" }],
        },
      ],
    };
    const code = run(tree).children[0] as never as { children: unknown[] };
    expect(code.children).toEqual([{ type: "text", value: "arr[6]" }]);
  });

  it("leaves markers inside a pre block untouched", () => {
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "pre",
          properties: {},
          children: [
            {
              type: "element",
              tagName: "code",
              properties: {},
              children: [{ type: "text", value: "x = y[7]" }],
            },
          ],
        },
      ],
    };
    const pre = run(tree).children[0] as never as {
      children: [{ children: unknown[] }];
    };
    expect(pre.children[0].children).toEqual([{ type: "text", value: "x = y[7]" }]);
  });
});
