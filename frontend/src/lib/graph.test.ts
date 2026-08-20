import { describe, expect, it } from "vitest";
import {
  BOUNDS,
  addPaper,
  clampPosition,
  clearGraph,
  countLabel,
  degree,
  degreeLabel,
  edgeAriaLabel,
  edgeGeometry,
  edgeId,
  edgeLabel,
  edgeWeight,
  formatDistance,
  initialGraphState,
  isEmpty,
  liveEdges,
  nodeEdgeRows,
  nudge,
  onCanvas,
  paperById,
  pickerRows,
  pointerToPercent,
  movedEnough,
  select,
  summarize,
  removePaper,
  type GraphState,
} from "./graph";
import { GRAPH_EDGES, GRAPH_PAPERS } from "./graph-data";

/** A canvas holding exactly these papers, each at its home position. */
function withOnly(...ids: string[]): GraphState {
  return ids.reduce((s, id) => addPaper(s, id), clearGraph());
}

describe("the corpus", () => {
  it("is the concept's four placeable papers", () => {
    expect(GRAPH_PAPERS.map((p) => p.id)).toEqual(["p1", "p2", "p3", "p4"]);
  });

  it("draws no edge looser than the stated cut", () => {
    for (const e of GRAPH_EDGES) expect(e.distance).toBeLessThan(0.75);
  });

  it("has both endpoints of every edge in the corpus", () => {
    for (const e of GRAPH_EDGES) {
      expect(paperById(e.a)).toBeDefined();
      expect(paperById(e.b)).toBeDefined();
    }
  });

  it("gives every edge a stable id that does not move with the live list", () => {
    const ids = GRAPH_EDGES.map(edgeId);
    expect(new Set(ids).size).toBe(ids.length);
    // p1--p2 keeps its id when the canvas loses a paper it has nothing to do with
    const full = initialGraphState();
    const without = removePaper(full, "p3");
    const before = liveEdges(full).find((e) => e.a === "p1" && e.b === "p2");
    const after = liveEdges(without).find((e) => e.a === "p1" && e.b === "p2");
    expect(edgeId(before!)).toBe(edgeId(after!));
  });
});

describe("canvas membership", () => {
  it("starts with every embedded paper at its home position", () => {
    const s = initialGraphState();
    expect(onCanvas(s).map((p) => p.id)).toEqual(["p1", "p2", "p3", "p4"]);
    expect(s.positions.p1).toEqual({ x: 21, y: 24 });
    expect(s.selection).toBeNull();
  });

  it("reports empty ONLY when nothing is on the canvas", () => {
    expect(isEmpty(clearGraph())).toBe(true);
    expect(isEmpty(initialGraphState())).toBe(false);
    expect(isEmpty(withOnly("p3"))).toBe(false);
    // and a canvas emptied one node at a time is empty again
    const drained = GRAPH_PAPERS.reduce(
      (s, p) => removePaper(s, p.id),
      initialGraphState()
    );
    expect(isEmpty(drained)).toBe(true);
  });

  it("orders nodes by the corpus, not by the clicks that produced them", () => {
    expect(onCanvas(withOnly("p4", "p1", "p3")).map((p) => p.id)).toEqual([
      "p1",
      "p3",
      "p4",
    ]);
  });

  it("places an added paper at its home position", () => {
    const s = addPaper(clearGraph(), "p2");
    expect(s.positions.p2).toEqual({ x: 67, y: 70 });
  });

  it("does not snap a dragged node home when its paper is added again", () => {
    const dragged: GraphState = {
      positions: { p1: { x: 50, y: 50 } },
      selection: null,
    };
    expect(addPaper(dragged, "p1").positions.p1).toEqual({ x: 50, y: 50 });
  });

  it("ignores an id no paper claims", () => {
    const s = clearGraph();
    expect(addPaper(s, "nope")).toBe(s);
  });

  it("copies the home position rather than aliasing it", () => {
    const s = addPaper(clearGraph(), "p1");
    s.positions.p1.x = 99;
    expect(GRAPH_PAPERS[0].home.x).toBe(21);
  });
});

describe("removing a node", () => {
  it("takes its edges with it", () => {
    const full = initialGraphState();
    expect(liveEdges(full)).toHaveLength(4);
    const without = removePaper(full, "p1");
    expect(liveEdges(without).map(edgeId)).toEqual(["p2--p4"]);
  });

  it("moves nothing else", () => {
    const dragged: GraphState = {
      positions: {
        p1: { x: 11, y: 12 },
        p2: { x: 33, y: 34 },
        p4: { x: 88, y: 66 },
      },
      selection: null,
    };
    const after = removePaper(dragged, "p2");
    expect(after.positions).toEqual({
      p1: { x: 11, y: 12 },
      p4: { x: 88, y: 66 },
    });
  });

  it("closes a detail panel that was open on it", () => {
    const s = select(initialGraphState(), { kind: "node", id: "p3" });
    expect(removePaper(s, "p3").selection).toBeNull();
  });

  it("closes a detail panel open on an edge it just erased", () => {
    const s = select(initialGraphState(), { kind: "edge", id: "p1--p3" });
    expect(removePaper(s, "p3").selection).toBeNull();
  });

  it("leaves a detail panel open on something still drawn", () => {
    const s = select(initialGraphState(), { kind: "edge", id: "p1--p4" });
    expect(removePaper(s, "p3").selection).toEqual({ kind: "edge", id: "p1--p4" });
  });

  it("is a no-op for a paper that is not on the canvas", () => {
    const s = withOnly("p1");
    expect(removePaper(s, "p2")).toBe(s);
  });
});

describe("edges and degree", () => {
  it("draws an edge only when both endpoints are on the canvas", () => {
    expect(liveEdges(withOnly("p1")).map(edgeId)).toEqual([]);
    expect(liveEdges(withOnly("p1", "p4")).map(edgeId)).toEqual(["p1--p4"]);
  });

  it("counts the edges touching a node", () => {
    const full = initialGraphState();
    expect(degree(full, "p1")).toBe(3);
    expect(degree(full, "p3")).toBe(1);
    expect(degree(withOnly("p2", "p3"), "p3")).toBe(0);
  });

  it("weights an edge by nearness, not by the cut", () => {
    expect(edgeWeight({ ...GRAPH_EDGES[0], distance: 0.59 })).toBe("near");
    expect(edgeWeight({ ...GRAPH_EDGES[0], distance: 0.64999 })).toBe("near");
    expect(edgeWeight({ ...GRAPH_EDGES[0], distance: 0.65 })).toBe("far");
    expect(edgeWeight({ ...GRAPH_EDGES[0], distance: 0.74 })).toBe("far");
  });

  it("labels an edge with its distance and facet at a fixed precision", () => {
    expect(formatDistance(0.7)).toBe("0.70");
    expect(edgeLabel(GRAPH_EDGES[2])).toBe("0.70 · evidence");
    expect(edgeLabel(GRAPH_EDGES[0])).toBe("0.59 · setting");
  });

  it("names both endpoints in the label a screen reader hears", () => {
    expect(edgeAriaLabel(GRAPH_EDGES[0])).toBe(
      "Edge: Cooperative Multi-Target Search and Voronoi Partitioning, distance 0.59, shared facet setting"
    );
  });

  it("lists a node's own links for its detail panel", () => {
    expect(nodeEdgeRows(initialGraphState(), "p3")).toEqual([
      { title: "Cooperative Multi-Target Search", label: "0.66 · evidence" },
    ]);
    expect(nodeEdgeRows(withOnly("p2", "p3"), "p3")).toEqual([]);
  });
});

describe("geometry", () => {
  it("anchors an edge on the SAME numbers the nodes are placed with", () => {
    const s: GraphState = {
      positions: { p1: { x: 20, y: 40 }, p4: { x: 60, y: 80 } },
      selection: null,
    };
    const g = edgeGeometry(s, GRAPH_EDGES[0])!;
    expect(g.x1).toBe(s.positions.p1.x);
    expect(g.y1).toBe(s.positions.p1.y);
    expect(g.x2).toBe(s.positions.p4.x);
    expect(g.y2).toBe(s.positions.p4.y);
  });

  it("puts an edge's label at the midpoint of its line", () => {
    const s: GraphState = {
      positions: { p1: { x: 20, y: 40 }, p4: { x: 60, y: 80 } },
      selection: null,
    };
    expect(edgeGeometry(s, GRAPH_EDGES[0])).toEqual({
      x1: 20,
      y1: 40,
      x2: 60,
      y2: 80,
      mx: 40,
      my: 60,
    });
  });

  it("follows a dragged node without anything having to be measured", () => {
    const before = edgeGeometry(initialGraphState(), GRAPH_EDGES[0])!;
    const dragged = {
      ...initialGraphState(),
      positions: { ...initialGraphState().positions, p1: { x: 80, y: 15 } },
    };
    const after = edgeGeometry(dragged, GRAPH_EDGES[0])!;
    expect(before.x1).toBe(21);
    expect(after.x1).toBe(80);
    expect(after.mx).toBe((80 + 63) / 2);
  });

  it("refuses to place a half-anchored line", () => {
    expect(edgeGeometry(withOnly("p1"), GRAPH_EDGES[0])).toBeNull();
  });

  it("keeps a dropped node inside the box", () => {
    expect(clampPosition({ x: -40, y: 200 })).toEqual({
      x: BOUNDS.minX,
      y: BOUNDS.maxY,
    });
    expect(clampPosition({ x: 50, y: 50 })).toEqual({ x: 50, y: 50 });
  });

  it("carries a node under the pointer and clamps where it lands", () => {
    const rect = { left: 100, top: 50, width: 800, height: 400 };
    expect(pointerToPercent(500, 250, rect)).toEqual({ x: 50, y: 50 });
    // dragged past the right edge, it stops at the bound rather than leaving
    expect(pointerToPercent(2000, 250, rect)).toEqual({ x: BOUNDS.maxX, y: 50 });
  });

  it("tells a drag apart from a click, so a drop does not open a panel", () => {
    expect(movedEnough({ x: 50, y: 50 }, { x: 50.2, y: 50.1 })).toBe(false);
    expect(movedEnough({ x: 50, y: 50 }, { x: 51, y: 50 })).toBe(true);
    expect(movedEnough({ x: 50, y: 50 }, { x: 50, y: 48 })).toBe(true);
  });

  it("carries the node with the cursor rather than teleporting it", () => {
    const rect = { left: 0, top: 0, width: 1000, height: 500 };
    // grabbed 4% left of and 3% above the node's centre
    expect(pointerToPercent(500, 250, rect, { x: 4, y: 3 })).toEqual({
      x: 54,
      y: 53,
    });
  });

  it("survives a canvas that has not been measured yet", () => {
    expect(
      pointerToPercent(10, 10, { left: 0, top: 0, width: 0, height: 0 })
    ).toEqual({ x: BOUNDS.minX, y: BOUNDS.minY });
  });
});

describe("the keyboard path for moving a node", () => {
  it("steps in the arrow's direction", () => {
    expect(nudge({ x: 50, y: 50 }, "ArrowLeft")).toEqual({ x: 48, y: 50 });
    expect(nudge({ x: 50, y: 50 }, "ArrowRight")).toEqual({ x: 52, y: 50 });
    expect(nudge({ x: 50, y: 50 }, "ArrowUp")).toEqual({ x: 50, y: 48 });
    expect(nudge({ x: 50, y: 50 }, "ArrowDown")).toEqual({ x: 50, y: 52 });
  });

  it("stops at the same bounds a drag does", () => {
    expect(nudge({ x: BOUNDS.minX, y: 50 }, "ArrowLeft")).toEqual({
      x: BOUNDS.minX,
      y: 50,
    });
  });

  it("returns null for a key it must not swallow", () => {
    expect(nudge({ x: 50, y: 50 }, "Tab")).toBeNull();
    expect(nudge({ x: 50, y: 50 }, "Enter")).toBeNull();
  });
});

describe("what the canvas says about itself", () => {
  it("counts papers and edges, singular and plural", () => {
    expect(countLabel(4, 4)).toBe("4 papers · 4 edges");
    expect(countLabel(1, 0)).toBe("1 paper · 0 edges");
    expect(countLabel(2, 1)).toBe("2 papers · 1 edge");
  });

  it("spells out a node with no edges rather than showing a zero", () => {
    expect(degreeLabel(0)).toBe("no edges above the cut");
    expect(degreeLabel(1)).toBe("1 edge");
    expect(degreeLabel(3)).toBe("3 edges");
  });

  it("says nothing about an empty canvas", () => {
    expect(summarize(clearGraph())).toEqual({ kind: "empty" });
  });

  it("reports an isolated paper as a finding", () => {
    const s = summarize(withOnly("p2", "p3"));
    expect(s.kind).toBe("isolated");
    if (s.kind !== "isolated") throw new Error("unreachable");
    expect(s.names).toEqual(["Decentralised Task Reallocation", "Hybrid Split-Federated Learning"]);
    expect(s.verb).toBe("share nothing");
    expect(s.tail).toContain("not a rendering failure");
  });

  it("uses the singular verb for one isolated paper", () => {
    const s = summarize(withOnly("p2", "p3", "p4"));
    if (s.kind !== "isolated") throw new Error("expected an isolated summary");
    expect(s.names).toEqual(["Hybrid Split-Federated Learning"]);
    expect(s.verb).toBe("shares nothing");
  });

  it("names the thinnest attachment when everything is connected", () => {
    const s = summarize(initialGraphState());
    if (s.kind !== "connected") throw new Error("expected a connected summary");
    expect(s.weakest).toBe("Hybrid Split-Federated Learning");
    expect(s.degree).toBe(1);
    expect(s.tail).toContain("hangs on 1 edge");
  });

  it("breaks a tie on corpus order, so the same canvas names the same paper", () => {
    const a = summarize(withOnly("p1", "p4"));
    const b = summarize(withOnly("p4", "p1"));
    expect(a).toEqual(b);
    if (a.kind !== "connected") throw new Error("expected a connected summary");
    expect(a.weakest).toBe("Cooperative Multi-Target Search");
  });
});

describe("the picker", () => {
  it("offers every embedded paper and marks the ones already placed", () => {
    expect(pickerRows(withOnly("p2"))).toEqual([
      { id: "p1", title: "Cooperative Multi-Target Search", onCanvas: false },
      { id: "p2", title: "Decentralised Task Reallocation", onCanvas: true },
      { id: "p3", title: "Hybrid Split-Federated Learning", onCanvas: false },
      { id: "p4", title: "Voronoi Partitioning", onCanvas: false },
    ]);
  });
});
