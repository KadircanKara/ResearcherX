"use client";

import { useRef } from "react";
import type { GraphEdge, GraphNode, GraphPaper } from "@/lib/graph-data";
import {
  CANVAS_H,
  CANVAS_W,
  arrowDelta,
  clampToCanvas,
  type GraphSelection,
} from "@/lib/graph";
import { GraphNodeShape } from "@/components/graph/graph-node";
import { GraphEdgeLine } from "@/components/graph/graph-edge-line";

/**
 * The canvas: a fixed 900 x 520 SVG coordinate space on a dotted card. Drag a
 * node (pointer capture on the node) or focus it and use the arrow keys, Shift
 * for a bigger step; either way it stays inside the canvas. Clicking empty
 * canvas clears the selection.
 */
export function GraphCanvas({
  nodes,
  edges,
  papersById,
  selection,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  onMoveNode,
  onRemoveNode,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  papersById: ReadonlyMap<string, GraphPaper>;
  selection: GraphSelection;
  onSelectNode: (paperId: string) => void;
  onSelectEdge: (edgeId: string) => void;
  onClearSelection: () => void;
  onMoveNode: (paperId: string, x: number, y: number) => void;
  onRemoveNode: (paperId: string) => void;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragging = useRef<{ paperId: string; offsetX: number; offsetY: number } | null>(null);

  function toSvgPoint(clientX: number, clientY: number) {
    const svg = svgRef.current;
    if (!svg) return { x: clientX, y: clientY };
    const ctm = svg.getScreenCTM();
    if (!ctm) return { x: clientX, y: clientY };
    const pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const transformed = pt.matrixTransform(ctm.inverse());
    return { x: transformed.x, y: transformed.y };
  }

  function handlePointerDown(e: React.PointerEvent<SVGGElement>, node: GraphNode) {
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = toSvgPoint(e.clientX, e.clientY);
    dragging.current = { paperId: node.paperId, offsetX: p.x - node.x, offsetY: p.y - node.y };
    onSelectNode(node.paperId);
  }

  function handlePointerMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!dragging.current) return;
    const p = toSvgPoint(e.clientX, e.clientY);
    const clamped = clampToCanvas(p.x - dragging.current.offsetX, p.y - dragging.current.offsetY);
    onMoveNode(dragging.current.paperId, clamped.x, clamped.y);
  }

  function handlePointerUp() {
    dragging.current = null;
  }

  function handleArrowKey(e: React.KeyboardEvent<SVGGElement>, node: GraphNode) {
    const delta = arrowDelta(e.key, e.shiftKey);
    if (!delta) return;
    e.preventDefault();
    const clamped = clampToCanvas(node.x + delta.dx, node.y + delta.dy);
    onMoveNode(node.paperId, clamped.x, clamped.y);
  }

  const dotPatternId = "graph-canvas-dots";

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
      className="h-[520px] w-full touch-none rounded-lg border bg-card"
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onClick={(e) => {
        if (e.target === svgRef.current) onClearSelection();
      }}
    >
      <defs>
        <pattern id={dotPatternId} width={22} height={22} patternUnits="userSpaceOnUse">
          <circle cx={2} cy={2} r={1.2} className="fill-muted-foreground/30" />
        </pattern>
      </defs>
      <rect
        x={0}
        y={0}
        width={CANVAS_W}
        height={CANVAS_H}
        fill={`url(#${dotPatternId})`}
        onClick={onClearSelection}
      />
      {edges.map((edge) => {
        const nodeA = nodes.find((n) => n.paperId === edge.a);
        const nodeB = nodes.find((n) => n.paperId === edge.b);
        if (!nodeA || !nodeB) return null;
        return (
          <GraphEdgeLine
            key={edge.id}
            edge={edge}
            nodeA={nodeA}
            nodeB={nodeB}
            selected={selection?.kind === "edge" && selection.edgeId === edge.id}
            onSelect={(e) => {
              e.stopPropagation();
              onSelectEdge(edge.id);
            }}
          />
        );
      })}
      {nodes.map((node) => (
        <GraphNodeShape
          key={node.paperId}
          node={node}
          paper={papersById.get(node.paperId)}
          selected={selection?.kind === "node" && selection.paperId === node.paperId}
          onPointerDown={(e) => handlePointerDown(e, node)}
          onClick={(e) => {
            e.stopPropagation();
            onSelectNode(node.paperId);
          }}
          onKeyDown={(e) => handleArrowKey(e, node)}
          onRemove={() => onRemoveNode(node.paperId)}
        />
      ))}
    </svg>
  );
}
