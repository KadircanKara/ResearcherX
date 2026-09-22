import { describe, expect, it } from "vitest";
import {
  GRAPH_EDGES,
  GRAPH_NODES,
  GRAPH_PAPERS,
  GRAPH_SPARE_NODES,
  GRAPH_UNAVAILABLE,
} from "./graph-data";

/**
 * The sample corpus is hand-written, so its cross-references are pinned here:
 * a typo in a paper id would otherwise draw nothing, silently.
 */
const paperIds = new Set(GRAPH_PAPERS.map((p) => p.id));

describe("the sample graph corpus", () => {
  it("has unique paper, node and edge ids", () => {
    expect(paperIds.size).toBe(GRAPH_PAPERS.length);
    const placed = [...GRAPH_NODES, ...GRAPH_SPARE_NODES].map((n) => n.paperId);
    expect(new Set(placed).size).toBe(placed.length);
    expect(new Set(GRAPH_EDGES.map((e) => e.id)).size).toBe(GRAPH_EDGES.length);
  });

  it("places only papers that are in the library and not refused", () => {
    const refused = new Set(GRAPH_UNAVAILABLE.map((u) => u.paperId));
    for (const n of [...GRAPH_NODES, ...GRAPH_SPARE_NODES]) {
      expect(paperIds.has(n.paperId), n.paperId).toBe(true);
      expect(refused.has(n.paperId), n.paperId).toBe(false);
    }
  });

  it("gives every library paper exactly one of: a start spot, a spare spot, a refusal", () => {
    const covered = [
      ...GRAPH_NODES.map((n) => n.paperId),
      ...GRAPH_SPARE_NODES.map((n) => n.paperId),
      ...GRAPH_UNAVAILABLE.map((u) => u.paperId),
    ].sort();
    expect(covered).toEqual([...paperIds].sort());
  });

  it("draws edges only between papers that can be placed", () => {
    const placeable = new Set([...GRAPH_NODES, ...GRAPH_SPARE_NODES].map((n) => n.paperId));
    for (const e of GRAPH_EDGES) {
      expect(placeable.has(e.a), `${e.id}.a`).toBe(true);
      expect(placeable.has(e.b), `${e.id}.b`).toBe(true);
      expect(e.a).not.toBe(e.b);
      expect(e.similarity).toBeGreaterThanOrEqual(0);
      expect(e.similarity).toBeLessThanOrEqual(1);
    }
  });
});
