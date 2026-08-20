"use client";

import { useRef, type PointerEvent as ReactPointerEvent } from "react";
import {
  degree,
  degreeLabel,
  edgeAriaLabel,
  edgeGeometry,
  edgeId,
  edgeLabel,
  edgeWeight,
  isEmpty,
  liveEdges,
  movedEnough,
  nudge,
  onCanvas,
  pointerToPercent,
  type GraphState,
  type Point,
} from "@/lib/graph";
import { GRAPH_COPY } from "@/lib/graph-data";

/**
 * The canvas: inline SVG for the edges, positioned buttons for the nodes and
 * the edge labels. No graph library, no CDN, no simulation — a node is where
 * its paper's home position says or where it was dropped, and nothing on the
 * canvas moves unless the reader moves it.
 *
 * The SVG's user space IS the percent space the node positions are in
 * (`viewBox="0 0 100 100"`, `preserveAspectRatio="none"`, a non-scaling
 * stroke), so nothing measures the box and an edge cannot disagree with the
 * node it is anchored to. Every rule the canvas obeys lives in `lib/graph.ts`
 * and is tested; this file is the wiring.
 */
export function GraphCanvas({
  state,
  onMove,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  onRemove,
}: {
  state: GraphState;
  onMove: (id: string, point: Point) => void;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
  onRemove: (id: string) => void;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    id: string;
    rect: DOMRect;
    offset: Point;
    start: Point;
    moved: boolean;
  } | null>(null);
  // A drag that ends on a node also fires a click on it. Without this the drop
  // would open that node's detail panel every time.
  const swallowClick = useRef(false);

  function handlePointerDown(e: ReactPointerEvent<HTMLButtonElement>, id: string) {
    if (e.button !== 0) return;
    const canvas = canvasRef.current;
    const at = state.positions[id];
    if (!canvas || !at) return;
    const rect = canvas.getBoundingClientRect();
    const under = pointerToPercent(e.clientX, e.clientY, rect);
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = {
      id,
      rect,
      offset: { x: at.x - under.x, y: at.y - under.y },
      start: at,
      moved: false,
    };
  }

  function handlePointerMove(e: ReactPointerEvent<HTMLButtonElement>) {
    const d = drag.current;
    if (!d) return;
    const next = pointerToPercent(e.clientX, e.clientY, d.rect, d.offset);
    if (!d.moved && !movedEnough(d.start, next)) return;
    d.moved = true;
    onMove(d.id, next);
  }

  function handlePointerUp(e: ReactPointerEvent<HTMLButtonElement>) {
    const d = drag.current;
    if (!d) return;
    swallowClick.current = d.moved;
    drag.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  }

  const nodes = onCanvas(state);
  const edges = liveEdges(state);

  return (
    <div
      ref={canvasRef}
      className="rx-gcanvas"
      onClick={(e) => {
        // Only a click on the canvas itself clears the selection; the SVG is
        // pointer-events:none, so an edge line never counts as background.
        if (e.target === e.currentTarget) onClearSelection();
      }}
    >
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {edges.map((edge) => {
          const g = edgeGeometry(state, edge);
          if (!g) return null;
          return (
            <line
              key={edgeId(edge)}
              x1={g.x1}
              y1={g.y1}
              x2={g.x2}
              y2={g.y2}
              vectorEffect="non-scaling-stroke"
              className={
                edgeWeight(edge) === "near" ? "rx-eline rx-eline-near" : "rx-eline"
              }
            />
          );
        })}
      </svg>

      {edges.map((edge) => {
        const g = edgeGeometry(state, edge);
        if (!g) return null;
        const id = edgeId(edge);
        const selected = state.selection?.kind === "edge" && state.selection.id === id;
        return (
          <button
            key={id}
            type="button"
            className={selected ? "rx-elabel rx-elabel-sel" : "rx-elabel"}
            style={{ left: `${g.mx}%`, top: `${g.my}%` }}
            aria-label={edgeAriaLabel(edge)}
            aria-expanded={selected}
            onClick={() => onSelectEdge(id)}
          >
            {edgeLabel(edge)}
          </button>
        );
      })}

      {nodes.map((paper) => {
        const at = state.positions[paper.id];
        const selected =
          state.selection?.kind === "node" && state.selection.id === paper.id;
        return (
          <div
            key={paper.id}
            className="rx-gnode-box"
            style={{ left: `${at.x}%`, top: `${at.y}%` }}
          >
            <button
              type="button"
              className={selected ? "rx-gnode rx-gnode-sel" : "rx-gnode"}
              title={paper.full}
              aria-expanded={selected}
              onPointerDown={(e) => handlePointerDown(e, paper.id)}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerCancel={handlePointerUp}
              onKeyDown={(e) => {
                // Arrow keys are the keyboard's drag. Anything else — Tab above
                // all — is left alone.
                const moved = nudge(at, e.key);
                if (!moved) return;
                e.preventDefault();
                onMove(paper.id, moved);
              }}
              onClick={() => {
                if (swallowClick.current) {
                  swallowClick.current = false;
                  return;
                }
                onSelectNode(paper.id);
              }}
            >
              <span className="rx-nt">{paper.short}</span>
              <span className="rx-nb">{paper.byline}</span>
              <span className="rx-ne">{degreeLabel(degree(state, paper.id))}</span>
            </button>
            <button
              type="button"
              className="rx-gx"
              aria-label={`Remove ${paper.short} from the graph`}
              onClick={() => onRemove(paper.id)}
            >
              <svg
                width="10"
                height="10"
                viewBox="0 0 12 12"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <path d="M3 3l6 6M9 3l-6 6" />
              </svg>
            </button>
          </div>
        );
      })}

      {isEmpty(state) && (
        <div className="rx-gempty">
          <div>
            <h3>{GRAPH_COPY.emptyTitle}</h3>
            <p>{GRAPH_COPY.emptyBody}</p>
          </div>
        </div>
      )}
    </div>
  );
}
