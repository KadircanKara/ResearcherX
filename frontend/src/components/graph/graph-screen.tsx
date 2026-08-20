"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { GraphCanvas } from "@/components/graph/graph-canvas";
import { GraphDetail } from "@/components/graph/graph-detail";
import {
  addPaper,
  clearGraph,
  countLabel,
  initialGraphState,
  liveEdges,
  onCanvas,
  pickerRows,
  removePaper,
  select,
  summarize,
  type GraphState,
  type Point,
} from "@/lib/graph";
import { GRAPH_COPY, GRAPH_UNAVAILABLE } from "@/lib/graph-data";

/**
 * The Graph screen, ported from the approved "Reading Room" concept.
 *
 * A DESIGN PREVIEW ON SAMPLE DATA. The papers and every distance on this
 * screen come from `lib/graph-data.ts` and are the concept's own; they are not
 * this project's library and are not measurements of anything. The claim is
 * made once, in `GRAPH_COPY.preview`, next to the paragraph that introduces the
 * numbers — where the reader meets them, and not repeated on every row.
 *
 * All the rules live in `lib/graph.ts`, where they are tested. This component
 * holds state and wiring.
 */
export function GraphScreen() {
  const [state, setState] = useState<GraphState>(initialGraphState);

  // Removing a node with its × takes the focused element out of the DOM. The
  // picker row for the same paper is where the reader would go to put it back,
  // so focus follows it there rather than falling to the document body.
  const pickerRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const focusAfterRemove = useRef<string | null>(null);
  useEffect(() => {
    const id = focusAfterRemove.current;
    if (!id) return;
    focusAfterRemove.current = null;
    pickerRefs.current[id]?.focus();
  }, [state]);

  const move = useCallback((id: string, point: Point) => {
    setState((s) =>
      s.positions[id] ? { ...s, positions: { ...s.positions, [id]: point } } : s
    );
  }, []);

  const remove = useCallback((id: string, returnFocus: boolean) => {
    if (returnFocus) focusAfterRemove.current = id;
    setState((s) => removePaper(s, id));
  }, []);

  const papers = onCanvas(state);
  const edges = liveEdges(state);
  const summary = summarize(state);

  return (
    <>
      <div className="rx-head">
        <div>
          <div className="rx-eyebrow">{GRAPH_COPY.eyebrow}</div>
          <h1>{GRAPH_COPY.title}</h1>
        </div>
        <div className="rx-meta">
          {GRAPH_COPY.meta[0]}
          <br />
          {GRAPH_COPY.meta[1]}
        </div>
      </div>

      <div className="rx-gwrap">
        <div>
          <p className="rx-derived">{GRAPH_COPY.derived}</p>
          <p className="rx-preview">{GRAPH_COPY.preview}</p>

          <div className="rx-gbar">
            <button
              type="button"
              className="rx-btn rx-btn-ghost"
              onClick={() => setState(clearGraph())}
            >
              New graph
            </button>
            <span className="rx-gcount" aria-live="polite">
              {countLabel(papers.length, edges.length)}
            </span>
          </div>

          <GraphCanvas
            state={state}
            onMove={move}
            onSelectNode={(id) => setState((s) => select(s, { kind: "node", id }))}
            onSelectEdge={(id) => setState((s) => select(s, { kind: "edge", id }))}
            onClearSelection={() => setState((s) => select(s, null))}
            onRemove={(id) => remove(id, true)}
          />

          <p className="rx-gsummary">
            {summary.kind === "isolated" && (
              <>
                <b>{summary.names.join(", ")}</b> {summary.verb} {summary.tail}
              </>
            )}
            {summary.kind === "connected" && (
              <>
                {summary.lead} <b>{summary.weakest}</b> {summary.tail}
              </>
            )}
          </p>

          <GraphDetail state={state} />
        </div>

        <aside
          className="rx-rail rx-grail"
          aria-label="Add papers and read the threshold"
        >
          <div className="rx-flank-h">{GRAPH_COPY.pickerHeading}</div>
          {/* The picker is the keyboard path for add and remove: every paper
              has a row here whether or not it is on the canvas, and the button
              toggles. Nothing on this screen is reachable only by dragging. */}
          <ul className="rx-pick">
            {pickerRows(state).map((row) => (
              <li key={row.id}>
                <span className="rx-pt">{row.title}</span>
                <button
                  type="button"
                  ref={(el) => {
                    pickerRefs.current[row.id] = el;
                  }}
                  className={row.onCanvas ? "rx-mini rx-mini-on" : "rx-mini"}
                  aria-pressed={row.onCanvas}
                  onClick={() =>
                    row.onCanvas
                      ? remove(row.id, false)
                      : setState((s) => addPaper(s, row.id))
                  }
                >
                  {row.onCanvas ? "On canvas" : "Add"}
                </button>
              </li>
            ))}
            {/* Offered and refused, with the real reason. A paper with no
                embeddings has no distance to anything, so there is nothing
                honest to draw for it. */}
            {GRAPH_UNAVAILABLE.map((paper) => (
              <li key={paper.title} className="rx-off">
                <span className="rx-pt">{paper.title}</span>
                <button type="button" className="rx-mini" disabled>
                  Unavailable
                </button>
                <span className="rx-pw">{paper.why}</span>
              </li>
            ))}
          </ul>

          <div className="rx-flank-h rx-flank-h-next">
            {GRAPH_COPY.thresholdHeading}
          </div>
          <div className="rx-tot">{GRAPH_COPY.thresholdValue}</div>
          <p className="rx-flank-note">{GRAPH_COPY.thresholdNote}</p>
        </aside>
      </div>
    </>
  );
}
