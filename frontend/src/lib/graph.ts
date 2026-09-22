/**
 * The Graph screen's rules: canvas geometry, what is on the canvas, what the
 * rail offers, and the summary line under the canvas.
 *
 * All of it is pure and none of it is inside a component. vitest here runs in
 * the node environment with no jsdom, so logic left inside a component is logic
 * that is never pinned. Ported from the app prototype's `graph-preview.tsx`,
 * `graph-canvas.tsx`, `graph-edge-line.tsx` and `lib/graph-summary.ts`.
 *
 * Nothing here reads a clock, a DOM node or a random number. A node sits where
 * it was placed or dragged; nothing moves on its own.
 */

import {
  GRAPH_NODES,
  GRAPH_SPARE_NODES,
  type GraphEdge,
  type GraphNode,
  type GraphPaper,
  type UnavailablePaper,
} from "./graph-data";

/** The canvas's own coordinate space (the SVG viewBox). */
export const CANVAS_W = 900;
export const CANVAS_H = 520;

/** A node's box, centred on its x/y. */
export const NODE_W = 108;
export const NODE_H = 36;

/** One arrow-key step, and one with Shift held, in canvas units. */
export const STEP = 8;
export const STEP_SHIFT = 16;

/** Similarity below this doesn't count as "linked" for the summary line. */
export const GRAPH_LINK_THRESHOLD = 0.65;

/** Where a paper with no home position lands when added. */
const FALLBACK_SPOT = { x: 450, y: 260 } as const;

export type GraphSelection =
  | { kind: "node"; paperId: string }
  | { kind: "edge"; edgeId: string }
  | null;

/** Keep a node's whole box inside the canvas. */
export function clampToCanvas(x: number, y: number): { x: number; y: number } {
  const halfW = NODE_W / 2;
  const halfH = NODE_H / 2;
  return {
    x: Math.min(CANVAS_W - halfW, Math.max(halfW, x)),
    y: Math.min(CANVAS_H - halfH, Math.max(halfH, y)),
  };
}

/**
 * The move an arrow key asks for, or `null` for any other key (the caller then
 * leaves the event alone). Shift takes the larger step.
 */
export function arrowDelta(key: string, shift: boolean): { dx: number; dy: number } | null {
  const step = shift ? STEP_SHIFT : STEP;
  if (key === "ArrowUp") return { dx: 0, dy: -step };
  if (key === "ArrowDown") return { dx: 0, dy: step };
  if (key === "ArrowLeft") return { dx: -step, dy: 0 };
  if (key === "ArrowRight") return { dx: step, dy: 0 };
  return null;
}

/** The edges that can be drawn: both endpoints on the canvas. */
export function edgesOnCanvas(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[]
): GraphEdge[] {
  const present = new Set(nodes.map((n) => n.paperId));
  return edges.filter((e) => present.has(e.a) && present.has(e.b));
}

/**
 * The rail's two lists: papers that can be placed (library order), and the
 * refused ones paired with their reason (in the order the refusals are listed).
 * A refusal naming a paper that is not in the library is dropped.
 */
export function railPapers(
  papers: readonly GraphPaper[],
  unavailable: readonly UnavailablePaper[]
): {
  available: GraphPaper[];
  unavailable: { paper: GraphPaper; reason: string }[];
} {
  const refused = new Set(unavailable.map((u) => u.paperId));
  const byId = new Map(papers.map((p) => [p.id, p]));
  return {
    available: papers.filter((p) => !refused.has(p.id)),
    unavailable: unavailable.flatMap((u) => {
      const paper = byId.get(u.paperId);
      return paper ? [{ paper, reason: u.reason }] : [];
    }),
  };
}

export function moveNode(
  nodes: readonly GraphNode[],
  paperId: string,
  x: number,
  y: number
): GraphNode[] {
  return nodes.map((n) => (n.paperId === paperId ? { ...n, x, y } : n));
}

export function removeNode(nodes: readonly GraphNode[], paperId: string): GraphNode[] {
  return nodes.filter((n) => n.paperId !== paperId);
}

/** A selected node stops being selected when it leaves the canvas; nothing else changes. */
export function selectionAfterRemove(
  selection: GraphSelection,
  paperId: string
): GraphSelection {
  return selection?.kind === "node" && selection.paperId === paperId ? null : selection;
}

/**
 * A paper's home: its label and the spot it lands on when added. Papers placed
 * at first load keep the spot and label they started with, so "New graph" and
 * then "Add" puts "Ferrer 2024" back where it was rather than somewhere else
 * under a truncated title.
 */
export function homeNode(paperId: string): GraphNode | undefined {
  return (
    GRAPH_NODES.find((n) => n.paperId === paperId) ??
    GRAPH_SPARE_NODES.find((n) => n.paperId === paperId)
  );
}

/**
 * Put a paper on the canvas. A paper with a home lands there under its label.
 * One without (none in the sample corpus) takes the first spare spot not in
 * use, or the centre, under the first 16 characters of its title, as in the
 * prototype. Adding a paper already on the canvas changes nothing.
 */
export function addNode(
  nodes: readonly GraphNode[],
  paperId: string,
  papersById: ReadonlyMap<string, GraphPaper>
): GraphNode[] {
  if (nodes.some((n) => n.paperId === paperId)) return [...nodes];
  const home = homeNode(paperId);
  if (home) return [...nodes, { ...home }];
  const present = new Set(nodes.map((n) => n.paperId));
  const spare = GRAPH_SPARE_NODES.find((n) => !present.has(n.paperId));
  const spot = spare ? { x: spare.x, y: spare.y } : FALLBACK_SPOT;
  const label = papersById.get(paperId)?.title.slice(0, 16) ?? paperId;
  return [...nodes, { paperId, label, x: spot.x, y: spot.y }];
}

/** An edge's stroke thickens with similarity: 1 at 0, 4 at 1. */
export function edgeStrokeWidth(similarity: number): number {
  return 1 + similarity * 3;
}

/** Similarity as the canvas and the detail panel print it: two decimals. */
export function formatSimilarity(similarity: number): string {
  return similarity.toFixed(2);
}

/**
 * The line under the canvas: which papers are on it and how strongly they
 * connect. Checked in order: an empty canvas; the first node (in canvas order)
 * with no link at or above the threshold; otherwise the weakest link drawn.
 */
export function summariseGraph(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[]
): string {
  if (nodes.length === 0) {
    return "Nothing on the canvas yet — add a paper from the right.";
  }

  const onCanvas = edgesOnCanvas(nodes, edges);

  for (const node of nodes) {
    const linked = onCanvas.some(
      (e) =>
        (e.a === node.paperId || e.b === node.paperId) && e.similarity >= GRAPH_LINK_THRESHOLD
    );
    if (!linked) {
      return `${node.label} shares nothing above the threshold with the rest.`;
    }
  }

  if (onCanvas.length === 0) {
    return "Nothing on the canvas yet — add a paper from the right.";
  }

  const weakest = onCanvas.reduce((min, e) => (e.similarity < min.similarity ? e : min));
  const labelA = nodes.find((n) => n.paperId === weakest.a)?.label ?? weakest.a;
  const labelB = nodes.find((n) => n.paperId === weakest.b)?.label ?? weakest.b;
  return `Every paper on the canvas is linked; the weakest link is ${labelA} — ${labelB} at ${formatSimilarity(weakest.similarity)}.`;
}
