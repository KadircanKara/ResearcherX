import type { GraphEdge, GraphNode, GraphPaper } from "@/lib/graph-data";
import { formatSimilarity } from "@/lib/graph";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * The panel under the canvas: the selected paper (title, byline, facets and a
 * remove button), the selected link (similarity, shared facet, each paper's
 * claim and what separates them), or a prompt when nothing is selected.
 */
export function GraphDetailPanel({
  selection,
  node,
  edge,
  nodeA,
  nodeB,
  paper,
  onRemoveNode,
}: {
  selection: "node" | "edge" | null;
  node?: GraphNode | undefined;
  edge?: GraphEdge | undefined;
  nodeA?: GraphNode | undefined;
  nodeB?: GraphNode | undefined;
  paper?: GraphPaper | undefined;
  onRemoveNode?: ((paperId: string) => void) | undefined;
}) {
  if (selection === "node" && node && paper) {
    return (
      <div className="rounded-lg border p-4">
        <p className="text-sm font-semibold leading-snug">{paper.title}</p>
        <p className="mt-1 text-[13px] text-muted-foreground">
          {paper.authors.join(", ")} · {paper.year}
        </p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {paper.facets.map((facet) => (
            <Badge key={facet} variant="secondary">
              {facet}
            </Badge>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          onClick={() => onRemoveNode?.(node.paperId)}
        >
          Remove from canvas
        </Button>
      </div>
    );
  }

  if (selection === "edge" && edge && nodeA && nodeB) {
    return (
      <div className="rounded-lg border p-4">
        <p className="text-[13px] font-medium">
          {formatSimilarity(edge.similarity)} · shared facet: {edge.sharedFacet}
        </p>
        <div className="mt-3 space-y-2 text-[13px]">
          <p>
            <span className="font-semibold">{nodeA.label}: </span>
            <span className="text-muted-foreground">{edge.claimA}</span>
          </p>
          <p>
            <span className="font-semibold">{nodeB.label}: </span>
            <span className="text-muted-foreground">{edge.claimB}</span>
          </p>
        </div>
        <p className="mt-3 text-[13px] text-muted-foreground">{edge.separation}</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border p-4">
      <p className="text-[13px] text-muted-foreground">
        Select a paper or a link to read about it.
      </p>
    </div>
  );
}
