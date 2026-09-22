import { describe, expect, it } from "vitest";
import {
  CANVAS_H,
  CANVAS_W,
  GRAPH_LINK_THRESHOLD,
  NODE_H,
  NODE_W,
  STEP,
  STEP_SHIFT,
  addNode,
  arrowDelta,
  clampToCanvas,
  edgeStrokeWidth,
  edgesOnCanvas,
  formatSimilarity,
  homeNode,
  moveNode,
  railPapers,
  removeNode,
  selectionAfterRemove,
  summariseGraph,
} from "./graph";
import {
  GRAPH_EDGES,
  GRAPH_NODES,
  GRAPH_PAPERS,
  GRAPH_UNAVAILABLE,
  type GraphEdge,
  type GraphNode,
  type GraphPaper,
} from "./graph-data";

const PAPERS_BY_ID = new Map(GRAPH_PAPERS.map((p) => [p.id, p]));

function node(paperId: string, label = paperId, x = 100, y = 100): GraphNode {
  return { paperId, label, x, y };
}

function edge(id: string, a: string, b: string, similarity: number): GraphEdge {
  return {
    id,
    a,
    b,
    similarity,
    sharedFacet: "f",
    claimA: "",
    claimB: "",
    separation: "",
  };
}

describe("clampToCanvas", () => {
  it("leaves a point well inside the canvas alone", () => {
    expect(clampToCanvas(450, 260)).toEqual({ x: 450, y: 260 });
  });

  it("keeps the whole node box inside every edge", () => {
    expect(clampToCanvas(-50, -50)).toEqual({ x: NODE_W / 2, y: NODE_H / 2 });
    expect(clampToCanvas(5000, 5000)).toEqual({
      x: CANVAS_W - NODE_W / 2,
      y: CANVAS_H - NODE_H / 2,
    });
  });
});

describe("arrowDelta", () => {
  it("maps each arrow key to one step on its axis", () => {
    expect(arrowDelta("ArrowUp", false)).toEqual({ dx: 0, dy: -STEP });
    expect(arrowDelta("ArrowDown", false)).toEqual({ dx: 0, dy: STEP });
    expect(arrowDelta("ArrowLeft", false)).toEqual({ dx: -STEP, dy: 0 });
    expect(arrowDelta("ArrowRight", false)).toEqual({ dx: STEP, dy: 0 });
  });

  it("takes the larger step with Shift", () => {
    expect(arrowDelta("ArrowRight", true)).toEqual({ dx: STEP_SHIFT, dy: 0 });
  });

  it("returns null for any other key, so the event is left alone", () => {
    expect(arrowDelta("Enter", false)).toBeNull();
    expect(arrowDelta("a", true)).toBeNull();
  });
});

describe("edgesOnCanvas", () => {
  it("draws every sample edge at first load", () => {
    expect(edgesOnCanvas(GRAPH_NODES, GRAPH_EDGES).map((e) => e.id)).toEqual([
      "e1",
      "e2",
      "e3",
      "e4",
      "e5",
    ]);
  });

  it("drops an edge as soon as either endpoint leaves", () => {
    const nodes = removeNode(GRAPH_NODES, "p-reward");
    expect(edgesOnCanvas(nodes, GRAPH_EDGES).map((e) => e.id)).toEqual(["e1", "e2", "e3"]);
  });

  it("draws nothing on an empty canvas", () => {
    expect(edgesOnCanvas([], GRAPH_EDGES)).toEqual([]);
  });
});

describe("railPapers", () => {
  it("offers every sample paper that is not refused, in library order", () => {
    const { available } = railPapers(GRAPH_PAPERS, GRAPH_UNAVAILABLE);
    expect(available.map((p) => p.id)).toEqual([
      "p-coverage",
      "p-bandwidth",
      "p-reward",
      "p-mobility",
      "p-formation",
      "p-schedules",
      "p-benchmark",
      "p-safe",
    ]);
  });

  it("lists refusals in their own order, each with its paper and reason", () => {
    const { unavailable } = railPapers(GRAPH_PAPERS, GRAPH_UNAVAILABLE);
    expect(unavailable.map((u) => [u.paper.id, u.reason])).toEqual([
      ["p-fusion", "no indexed text"],
      ["p-allocation", "not checked yet"],
      ["p-hierarchical", "added before the similarity index existed"],
      ["p-relay", "not checked yet"],
    ]);
  });

  it("drops a refusal that names no paper in the library", () => {
    const { unavailable } = railPapers(GRAPH_PAPERS, [{ paperId: "nope", reason: "x" }]);
    expect(unavailable).toEqual([]);
  });
});

describe("moveNode / removeNode", () => {
  it("moves only the named node", () => {
    const moved = moveNode(GRAPH_NODES, "p-bandwidth", 10, 20);
    expect(moved.find((n) => n.paperId === "p-bandwidth")).toMatchObject({ x: 10, y: 20 });
    expect(moved.filter((n) => n.paperId !== "p-bandwidth")).toEqual(
      GRAPH_NODES.filter((n) => n.paperId !== "p-bandwidth")
    );
  });

  it("removes only the named node and keeps the rest in order", () => {
    expect(removeNode(GRAPH_NODES, "p-formation").map((n) => n.paperId)).toEqual([
      "p-coverage",
      "p-bandwidth",
      "p-reward",
    ]);
  });
});

describe("selectionAfterRemove", () => {
  it("clears the selection when the selected node leaves", () => {
    expect(selectionAfterRemove({ kind: "node", paperId: "a" }, "a")).toBeNull();
  });

  it("keeps a different node or an edge selected", () => {
    expect(selectionAfterRemove({ kind: "node", paperId: "b" }, "a")).toEqual({
      kind: "node",
      paperId: "b",
    });
    expect(selectionAfterRemove({ kind: "edge", edgeId: "e1" }, "a")).toEqual({
      kind: "edge",
      edgeId: "e1",
    });
    expect(selectionAfterRemove(null, "a")).toBeNull();
  });
});

describe("addNode", () => {
  it("puts a spare paper at its home spot under its label", () => {
    const nodes = addNode(GRAPH_NODES, "p-benchmark", PAPERS_BY_ID);
    expect(nodes.at(-1)).toEqual({ paperId: "p-benchmark", label: "Adeyemi 2025", x: 420, y: 230 });
  });

  it("puts a paper that started on the canvas back where it started, under its label", () => {
    const nodes = addNode([], "p-coverage", PAPERS_BY_ID);
    expect(nodes).toEqual([{ paperId: "p-coverage", label: "Ferrer 2024", x: 240, y: 150 }]);
  });

  it("does not duplicate a paper already on the canvas", () => {
    expect(addNode(GRAPH_NODES, "p-coverage", PAPERS_BY_ID)).toEqual(GRAPH_NODES);
  });

  it("falls back to the first free spare spot and a 16-character title for a paper with no home", () => {
    const paper: GraphPaper = {
      id: "p-new",
      title: "An Entirely New Paper About Swarms",
      authors: ["A. B."],
      year: 2026,
      facets: [],
    };
    const byId = new Map([[paper.id, paper]]);
    const nodes = addNode([node("p-benchmark")], "p-new", byId);
    expect(nodes.at(-1)).toEqual({ paperId: "p-new", label: "An Entirely New ", x: 180, y: 380 });
  });

  it("falls back to the centre once every spare spot is taken", () => {
    const taken = ["p-benchmark", "p-schedules", "p-safe", "p-mobility"].map((id) => node(id));
    expect(addNode(taken, "ghost", new Map()).at(-1)).toEqual({
      paperId: "ghost",
      label: "ghost",
      x: 450,
      y: 260,
    });
  });
});

describe("homeNode", () => {
  it("knows every paper the rail can place", () => {
    for (const p of railPapers(GRAPH_PAPERS, GRAPH_UNAVAILABLE).available) {
      expect(homeNode(p.id), p.id).toBeDefined();
    }
  });

  it("knows no refused paper", () => {
    for (const u of GRAPH_UNAVAILABLE) expect(homeNode(u.paperId)).toBeUndefined();
  });
});

describe("edge presentation", () => {
  it("thickens the stroke with similarity", () => {
    expect(edgeStrokeWidth(0)).toBe(1);
    expect(edgeStrokeWidth(1)).toBe(4);
    expect(edgeStrokeWidth(0.5)).toBeCloseTo(2.5);
  });

  it("prints similarity to two decimals", () => {
    expect(formatSimilarity(0.82)).toBe("0.82");
    expect(formatSimilarity(0.7)).toBe("0.70");
  });
});

describe("summariseGraph", () => {
  it("says the canvas is empty when nothing is on it", () => {
    expect(summariseGraph([], GRAPH_EDGES)).toBe(
      "Nothing on the canvas yet — add a paper from the right."
    );
  });

  it("names the weakest link when every paper is linked (the first-load canvas)", () => {
    expect(summariseGraph(GRAPH_NODES, GRAPH_EDGES)).toBe(
      "Every paper on the canvas is linked; the weakest link is Ferrer 2024b — Nowak 2023 at 0.61."
    );
  });

  it("names the first paper with no link at or above the threshold", () => {
    const nodes = [...GRAPH_NODES, { paperId: "p-benchmark", label: "Adeyemi 2025", x: 0, y: 0 }];
    expect(summariseGraph(nodes, GRAPH_EDGES)).toBe(
      "Adeyemi 2025 shares nothing above the threshold with the rest."
    );
  });

  it("treats a lone paper as unlinked", () => {
    expect(summariseGraph([node("a", "Solo 2024")], [])).toBe(
      "Solo 2024 shares nothing above the threshold with the rest."
    );
  });

  it("counts a link exactly at the threshold as linked", () => {
    const nodes = [node("a", "A"), node("b", "B")];
    expect(summariseGraph(nodes, [edge("x", "a", "b", GRAPH_LINK_THRESHOLD)])).toBe(
      "Every paper on the canvas is linked; the weakest link is A — B at 0.65."
    );
    expect(summariseGraph(nodes, [edge("x", "a", "b", GRAPH_LINK_THRESHOLD - 0.01)])).toBe(
      "A shares nothing above the threshold with the rest."
    );
  });

  it("ignores edges whose endpoints are not both on the canvas", () => {
    const nodes = [node("a", "A")];
    expect(summariseGraph(nodes, [edge("x", "a", "gone", 0.9)])).toBe(
      "A shares nothing above the threshold with the rest."
    );
  });
});
