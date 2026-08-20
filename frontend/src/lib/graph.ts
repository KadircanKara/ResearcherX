/**
 * The Graph screen's rules — geometry, edge derivation, canvas membership and
 * every string the canvas states about itself.
 *
 * All of it is pure and none of it is inside a component. This repo runs vitest
 * in the NODE environment with no jsdom, so it cannot test a component at all:
 * logic left inside one is logic that will never be pinned. The graph carries
 * more of it than anything else in this port, so the split is not cosmetic.
 *
 * Nothing here reads a clock, a DOM node or a random number. The layout is
 * deterministic by construction: a node is at its paper's HOME position or
 * wherever it was dragged, and no other node ever moves. There is no
 * simulation, no settling and nothing that animates on its own.
 */

import {
  GRAPH_EDGES,
  GRAPH_PAPERS,
  type GraphEdge,
  type GraphPaper,
} from "./graph-data";

export type Point = { x: number; y: number };

/** What the detail panel is showing. Node and edge ids share one field, so
 *  selecting either clears the other without a second piece of state. */
export type Selection =
  | { kind: "node"; id: string }
  | { kind: "edge"; id: string }
  | null;

export type GraphState = {
  /** Percent-of-canvas position per paper ON the canvas. A paper absent from
   *  this map is not on the canvas; a paper present is, wherever it sits. One
   *  map answers both questions, so the two can never disagree. */
  positions: Record<string, Point>;
  selection: Selection;
};

/**
 * How close a node may get to the canvas edge, in percent. The node is
 * translated by -50%/-50%, so an unclamped drop puts half of a 212px-wide
 * bubble outside the box; these are the concept's own limits.
 */
export const BOUNDS = { minX: 7, maxX: 93, minY: 9, maxY: 91 } as const;

/** One arrow-key step, in percent of the canvas. The keyboard path for moving
 *  a node; see `nudge`. */
export const NUDGE_STEP = 2;

const PAPERS_BY_ID: ReadonlyMap<string, GraphPaper> = new Map(
  GRAPH_PAPERS.map((p) => [p.id, p])
);

/** The paper behind a node id, or `undefined` for an id no corpus row claims. */
export function paperById(id: string): GraphPaper | undefined {
  return PAPERS_BY_ID.get(id);
}

/** A stable id for an edge, so a selection survives a node being added or
 *  removed. Deliberately not the edge's index in the live list: that index
 *  shifts under the selection whenever the canvas changes. */
export function edgeId(edge: GraphEdge): string {
  return `${edge.a}--${edge.b}`;
}

/** Every paper on the canvas, in CORPUS order rather than the order they were
 *  added. Insertion order would make the same set of nodes render in a
 *  different sequence depending on the clicks that produced it. */
export function onCanvas(state: GraphState): GraphPaper[] {
  return GRAPH_PAPERS.filter((p) => state.positions[p.id] !== undefined);
}

/** The canvas is empty when no paper is on it. The empty state renders on this
 *  answer and on nothing else. */
export function isEmpty(state: GraphState): boolean {
  return onCanvas(state).length === 0;
}

/** The edges that can be drawn: both endpoints on the canvas. Every edge in the
 *  corpus is already inside the cut, so this is the only filter there is. */
export function liveEdges(state: GraphState): GraphEdge[] {
  return GRAPH_EDGES.filter(
    (e) => state.positions[e.a] !== undefined && state.positions[e.b] !== undefined
  );
}

/** How many drawn edges touch a node. */
export function degree(state: GraphState, id: string): number {
  return liveEdges(state).filter((e) => e.a === id || e.b === id).length;
}

/** The initial canvas: every embedded paper at its home position, nothing
 *  selected. */
export function initialGraphState(): GraphState {
  const positions: Record<string, Point> = {};
  for (const p of GRAPH_PAPERS) positions[p.id] = { ...p.home };
  return { positions, selection: null };
}

/** "New graph": back to an empty canvas with no detail open. */
export function clearGraph(): GraphState {
  return { positions: {}, selection: null };
}

/** Adding places the paper at its home position. Adding a paper already on the
 *  canvas is a no-op — it must NOT snap a dragged node back home. */
export function addPaper(state: GraphState, id: string): GraphState {
  if (state.positions[id] !== undefined) return state;
  const paper = paperById(id);
  if (!paper) return state;
  return { ...state, positions: { ...state.positions, [id]: { ...paper.home } } };
}

/**
 * Removing takes the node and its edges and moves nothing else. The remaining
 * positions are carried through untouched, so a node that was dragged stays
 * exactly where it was dropped.
 */
export function removePaper(state: GraphState, id: string): GraphState {
  if (state.positions[id] === undefined) return state;
  const positions = { ...state.positions };
  delete positions[id];
  const next: GraphState = { positions, selection: state.selection };
  // A detail panel open on something no longer drawn is a panel about nothing.
  if (state.selection) {
    const stale =
      state.selection.kind === "node"
        ? state.selection.id === id
        : liveEdges(next).every((e) => edgeId(e) !== state.selection?.id);
    if (stale) next.selection = null;
  }
  return next;
}

export function select(state: GraphState, selection: Selection): GraphState {
  return { ...state, selection };
}

/** Where a node ends up after a drag or a nudge: inside the box, always. */
export function clampPosition(p: Point): Point {
  return {
    x: Math.min(BOUNDS.maxX, Math.max(BOUNDS.minX, p.x)),
    y: Math.min(BOUNDS.maxY, Math.max(BOUNDS.minY, p.y)),
  };
}

/**
 * A pointer position, in percent of the canvas box and already clamped.
 *
 * `offset` is the gap between the node's centre and where the pointer grabbed
 * it, so the node travels WITH the cursor instead of teleporting its centre
 * under it on the first move. Grabbing a node by its corner and having it jump
 * is the difference between dragging a thing and re-placing it.
 */
export function pointerToPercent(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  offset: Point = { x: 0, y: 0 }
): Point {
  if (rect.width === 0 || rect.height === 0) return clampPosition({ x: 0, y: 0 });
  return clampPosition({
    x: ((clientX - rect.left) / rect.width) * 100 + offset.x,
    y: ((clientY - rect.top) / rect.height) * 100 + offset.y,
  });
}

/**
 * The keyboard equivalent of a drag. Returns `null` for any key that is not an
 * arrow, so the caller knows whether to consume the event — a node must not
 * swallow Tab.
 */
export function nudge(p: Point, key: string, step = NUDGE_STEP): Point | null {
  switch (key) {
    case "ArrowLeft":
      return clampPosition({ x: p.x - step, y: p.y });
    case "ArrowRight":
      return clampPosition({ x: p.x + step, y: p.y });
    case "ArrowUp":
      return clampPosition({ x: p.x, y: p.y - step });
    case "ArrowDown":
      return clampPosition({ x: p.x, y: p.y + step });
    default:
      return null;
  }
}

export type EdgeGeometry = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  /** Midpoint — where the edge's label sits. */
  mx: number;
  my: number;
};

/**
 * An edge's line and label position, in PERCENT of the canvas box — the same
 * units a node's position is in, and the same units the SVG is given.
 *
 * The canvas SVG uses `viewBox="0 0 100 100"` with `preserveAspectRatio="none"`
 * and a non-scaling stroke, so percent IS the user space and there is no pixel
 * conversion anywhere in this screen: nothing has to measure the box, nothing
 * goes stale when it resizes, and there is no first-paint size of zero to
 * defend against. Node positions and edge endpoints therefore cannot disagree
 * about where a node is, because they are the same numbers.
 *
 * Returns `null` when either endpoint is off the canvas, so a caller cannot
 * draw a half-anchored line.
 */
export function edgeGeometry(state: GraphState, edge: GraphEdge): EdgeGeometry | null {
  const a = state.positions[edge.a];
  const b = state.positions[edge.b];
  if (!a || !b) return null;
  return {
    x1: a.x,
    y1: a.y,
    x2: b.x,
    y2: b.y,
    mx: (a.x + b.x) / 2,
    my: (a.y + b.y) / 2,
  };
}

/**
 * Did the pointer travel far enough for this to have been a drag rather than a
 * click? A drag that ends on a node would otherwise also open that node's
 * detail panel, because the click fires after the drag finishes.
 */
export function movedEnough(from: Point, to: Point, minPercent = 0.6): boolean {
  return Math.abs(to.x - from.x) >= minPercent || Math.abs(to.y - from.y) >= minPercent;
}

/**
 * Edge weight. The concept draws anything under 0.65 heavier, in the accent
 * line colour: nearness is the one thing an edge can say at a glance, before
 * its label has been read. The threshold is a rendering rule, not the cut —
 * the cut (0.75) is what got the edge into the corpus at all.
 */
export function edgeWeight(edge: GraphEdge): "near" | "far" {
  return edge.distance < 0.65 ? "near" : "far";
}

/** Two decimals, always: "0.7" would read as a different precision from
 *  "0.74" sitting next to it on the same canvas. */
export function formatDistance(distance: number): string {
  return distance.toFixed(2);
}

/** An edge's label: `0.59 · setting`. */
export function edgeLabel(edge: GraphEdge): string {
  return `${formatDistance(edge.distance)} · ${edge.facet}`;
}

/** What a screen reader hears instead of that label. The two titles are in it
 *  because an edge label read alone names neither of its endpoints. */
export function edgeAriaLabel(edge: GraphEdge): string {
  const a = paperById(edge.a)?.short ?? edge.a;
  const b = paperById(edge.b)?.short ?? edge.b;
  return `Edge: ${a} and ${b}, distance ${formatDistance(edge.distance)}, shared facet ${edge.facet}`;
}

/** The line under a node's byline. Zero is spelled out, because a node with no
 *  edges is a finding rather than a blank. */
export function degreeLabel(count: number): string {
  if (count === 0) return "no edges above the cut";
  return count === 1 ? "1 edge" : `${count} edges`;
}

/** The bar above the canvas: "4 papers · 4 edges". */
export function countLabel(papers: number, edges: number): string {
  const p = papers === 1 ? "1 paper" : `${papers} papers`;
  const e = edges === 1 ? "1 edge" : `${edges} edges`;
  return `${p} · ${e}`;
}

/**
 * The sentence under the canvas.
 *
 * `isolated` is the finding the concept cares about most: a paper sharing
 * nothing above the cut is a fact about the library, and the copy says so
 * rather than letting it read as a rendering failure. It is returned as
 * structured parts so the component can bold the titles without the rules
 * module emitting markup.
 */
export type GraphSummary =
  | { kind: "empty" }
  | { kind: "isolated"; names: string[]; verb: string; tail: string }
  | { kind: "connected"; weakest: string; degree: number; lead: string; tail: string };

export function summarize(state: GraphState): GraphSummary {
  const papers = onCanvas(state);
  if (papers.length === 0) return { kind: "empty" };

  const lonely = papers.filter((p) => degree(state, p.id) === 0);
  if (lonely.length > 0) {
    return {
      kind: "isolated",
      names: lonely.map((p) => p.short),
      verb: lonely.length === 1 ? "shares nothing" : "share nothing",
      tail: "above 0.75 with anything else on this canvas. That is a finding about your library, not a rendering failure.",
    };
  }

  // The thinnest attachment. Ties break on corpus order, which `onCanvas`
  // already fixes, so the same canvas always names the same paper.
  const weakest = papers.reduce((min, p) =>
    degree(state, p.id) < degree(state, min.id) ? p : min
  );
  const d = degree(state, weakest.id);
  return {
    kind: "connected",
    weakest: weakest.short,
    degree: d,
    lead: "Every paper here is connected.",
    tail: `hangs on ${degreeLabel(d)}, the thinnest attachment on the canvas — worth checking before you lean on it.`,
  };
}

/** One row of a node's detail panel: what it links to and how. */
export type NodeEdgeRow = { title: string; label: string };

export function nodeEdgeRows(state: GraphState, id: string): NodeEdgeRow[] {
  return liveEdges(state)
    .filter((e) => e.a === id || e.b === id)
    .map((e) => {
      const other = e.a === id ? e.b : e.a;
      return { title: paperById(other)?.short ?? other, label: edgeLabel(e) };
    });
}

/** The rail picker's placeable rows. Every embedded paper appears whether it is
 *  on the canvas or not — the picker is the add AND the remove control, and it
 *  is the keyboard path for both. */
export type PickerRow = { id: string; title: string; onCanvas: boolean };

export function pickerRows(state: GraphState): PickerRow[] {
  return GRAPH_PAPERS.map((p) => ({
    id: p.id,
    title: p.short,
    onCanvas: state.positions[p.id] !== undefined,
  }));
}
