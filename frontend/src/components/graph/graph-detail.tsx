"use client";

import {
  edgeId,
  formatDistance,
  liveEdges,
  nodeEdgeRows,
  paperById,
  type GraphState,
} from "@/lib/graph";

/**
 * The in-place expansion under the canvas — the concept's one animated thing.
 *
 * It stays mounted whether or not anything is selected, so the expansion has
 * something to expand: the reveal is a `grid-template-rows` transition from
 * `0fr` to `1fr`, which needs both ends of the animation to exist. When nothing
 * is selected the row collapses to zero height and its content is hidden from
 * assistive technology along with it.
 */
export function GraphDetail({ state }: { state: GraphState }) {
  const sel = state.selection;
  const open = sel !== null;

  return (
    <div className={open ? "rx-reveal rx-reveal-open" : "rx-reveal"}>
      <div>
        <div className="rx-reveal-body rx-gdetail" aria-hidden={!open}>
          {sel?.kind === "edge" && <EdgeDetail state={state} id={sel.id} />}
          {sel?.kind === "node" && <NodeDetail state={state} id={sel.id} />}
        </div>
      </div>
    </div>
  );
}

function EdgeDetail({ state, id }: { state: GraphState; id: string }) {
  const edge = liveEdges(state).find((e) => edgeId(e) === id);
  if (!edge) return null;
  const a = paperById(edge.a);
  const b = paperById(edge.b);
  if (!a || !b) return null;

  return (
    <>
      <div className="rx-src">
        <b>
          {a.short} ↔ {b.short}
        </b>{" "}
        <span className="rx-mono">
          {formatDistance(edge.distance)} · shared {edge.facet}
        </span>
      </div>
      <div className="rx-who2">
        <div>
          <div className="rx-t">{a.full}</div>
          <div className="rx-c">{edge.claimA}</div>
        </div>
        <div>
          <div className="rx-t">{b.full}</div>
          <div className="rx-c">{edge.claimB}</div>
        </div>
      </div>
      <p className="rx-sep">{edge.separates}</p>
      <p className="rx-flank-note">{edge.also}</p>
    </>
  );
}

function NodeDetail({ state, id }: { state: GraphState; id: string }) {
  const paper = paperById(id);
  if (!paper) return null;
  const rows = nodeEdgeRows(state, id);

  return (
    <>
      <div className="rx-src">
        <b>{paper.full}</b> <span>{paper.byline}</span>{" "}
        <span className="rx-mono">{paper.chunks} chunks</span>
      </div>
      <ul className="rx-fl-list">
        {rows.length === 0 ? (
          <li>
            <span className="rx-ttl">Nothing above the cut</span>
            <span className="rx-d">—</span>
          </li>
        ) : (
          rows.map((row) => (
            <li key={row.title}>
              <span className="rx-ttl">{row.title}</span>
              <span className="rx-d">{row.label}</span>
            </li>
          ))
        )}
      </ul>
    </>
  );
}

/* The concept's node panel ends with two links — "Ask a question about this
 * paper" and "Open in Papers". They are not ported. In the mockup they were
 * `href="#"` with the navigation cancelled; in a running app a link that looks
 * live and goes nowhere is the same lie as a fabricated distance, and these
 * sample papers are not in the user's library for either destination to reach.
 * They belong here the day the graph is drawn from real papers. */
