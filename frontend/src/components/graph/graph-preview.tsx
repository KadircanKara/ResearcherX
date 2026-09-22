"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { PageHeader } from "@/components/page-header";
import { GraphPreviewNote } from "@/components/graph/graph-preview-note";
import { GraphCanvas } from "@/components/graph/graph-canvas";
import { GraphDetailPanel } from "@/components/graph/graph-detail-panel";
import { GraphAddRail } from "@/components/graph/graph-add-rail";
import { Button } from "@/components/ui/button";
import { getProject } from "@/lib/projects";
import {
  addNode,
  edgesOnCanvas,
  moveNode,
  railPapers,
  removeNode,
  selectionAfterRemove,
  summariseGraph,
  type GraphSelection,
} from "@/lib/graph";
import {
  GRAPH_EDGES,
  GRAPH_NODES,
  GRAPH_PAPERS,
  GRAPH_UNAVAILABLE,
  type GraphNode,
} from "@/lib/graph-data";

const PAPERS_BY_ID = new Map(GRAPH_PAPERS.map((p) => [p.id, p]));
const RAIL = railPapers(GRAPH_PAPERS, GRAPH_UNAVAILABLE);

/**
 * The Graph tab, ported from the app prototype's `graph-preview.tsx`.
 *
 * A DESIGN PREVIEW ON SAMPLE DATA: the papers, links and similarities come from
 * `lib/graph-data.ts`, not from this project's library, and the preview note
 * says so on screen. The only backend call is the project lookup that names
 * the project in the eyebrow. Everything the reader does here (adding,
 * removing, dragging, "New graph") is local state and is gone on reload.
 */
export function GraphPreview() {
  const { id } = useParams<{ id: string }>();
  const [projectTitle, setProjectTitle] = useState<string | null>(null);
  const [nodes, setNodes] = useState<GraphNode[]>(() => [...GRAPH_NODES]);
  const [selection, setSelection] = useState<GraphSelection>(null);

  const railButtonRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    getProject(id)
      .then((detail) => {
        if (!cancelled) setProjectTitle(detail.project.title);
      })
      // The project layout owns the not-found state; here a failed lookup only
      // leaves the eyebrow blank.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [id]);

  const onCanvasIds = useMemo(() => new Set(nodes.map((n) => n.paperId)), [nodes]);
  const edges = useMemo(() => edgesOnCanvas(nodes, GRAPH_EDGES), [nodes]);

  function clearSelection() {
    setSelection(null);
  }

  function handleNewGraph() {
    setNodes([]);
    setSelection(null);
  }

  function handleMoveNode(paperId: string, x: number, y: number) {
    setNodes((prev) => moveNode(prev, paperId, x, y));
  }

  function handleRemoveNode(paperId: string) {
    setNodes((prev) => removeNode(prev, paperId));
    setSelection((prev) => selectionAfterRemove(prev, paperId));
    // The removed node (or its "×") had focus and is gone; the rail row for the
    // same paper is where the reader would put it back.
    requestAnimationFrame(() => {
      railButtonRefs.current[paperId]?.focus();
    });
  }

  function handleToggle(paperId: string) {
    if (onCanvasIds.has(paperId)) {
      handleRemoveNode(paperId);
      return;
    }
    setNodes((prev) => addNode(prev, paperId, PAPERS_BY_ID));
  }

  const selectedNode =
    selection?.kind === "node" ? nodes.find((n) => n.paperId === selection.paperId) : undefined;
  const selectedEdge =
    selection?.kind === "edge" ? edges.find((e) => e.id === selection.edgeId) : undefined;
  const selectedEdgeNodeA = selectedEdge
    ? nodes.find((n) => n.paperId === selectedEdge.a)
    : undefined;
  const selectedEdgeNodeB = selectedEdge
    ? nodes.find((n) => n.paperId === selectedEdge.b)
    : undefined;

  const summary = summariseGraph(nodes, edges);

  return (
    <div className="space-y-5">
      <PageHeader
        // A non-breaking space holds the eyebrow's line while the title loads.
        eyebrow={projectTitle ?? " "}
        title="A graph you built"
        meta={
          <>
            <p>Curated by you, not laid out for you</p>
            <p>Nodes stay where you put them</p>
          </>
        }
      />

      <p className="max-w-3xl text-[13px] text-muted-foreground">
        Distances on this canvas come from how each paper&apos;s excerpts embed — papers whose
        text lands nearby in that space are drawn close together, and a link only appears once two
        papers are similar enough to be worth comparing.
      </p>

      <GraphPreviewNote />

      <div className="flex flex-col gap-4 lg:flex-row">
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="ghost" size="sm" onClick={handleNewGraph}>
              New graph
            </Button>
            <p className="text-[12px] text-muted-foreground">
              {nodes.length} papers · {edges.length} links
            </p>
          </div>

          <GraphCanvas
            nodes={nodes}
            edges={edges}
            papersById={PAPERS_BY_ID}
            selection={selection}
            onSelectNode={(paperId) => setSelection({ kind: "node", paperId })}
            onSelectEdge={(edgeId) => setSelection({ kind: "edge", edgeId })}
            onClearSelection={clearSelection}
            onMoveNode={handleMoveNode}
            onRemoveNode={handleRemoveNode}
          />

          <p className="text-[13px] text-muted-foreground">{summary}</p>

          <GraphDetailPanel
            selection={selection?.kind ?? null}
            node={selectedNode}
            paper={selectedNode ? PAPERS_BY_ID.get(selectedNode.paperId) : undefined}
            edge={selectedEdge}
            nodeA={selectedEdgeNodeA}
            nodeB={selectedEdgeNodeB}
            onRemoveNode={handleRemoveNode}
          />
        </div>

        <GraphAddRail
          availablePapers={RAIL.available}
          onCanvas={onCanvasIds}
          onToggle={handleToggle}
          unavailable={RAIL.unavailable}
          rowRefs={railButtonRefs}
        />
      </div>
    </div>
  );
}
