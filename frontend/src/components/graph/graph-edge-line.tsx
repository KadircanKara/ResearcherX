import type { GraphEdge, GraphNode } from "@/lib/graph-data";
import { edgeStrokeWidth, formatSimilarity } from "@/lib/graph";

/**
 * One link: a wide transparent hit line (the button), the visible line under
 * it, and the similarity in a small card at the midpoint.
 */
export function GraphEdgeLine({
  edge,
  nodeA,
  nodeB,
  selected,
  onSelect,
}: {
  edge: GraphEdge;
  nodeA: GraphNode;
  nodeB: GraphNode;
  selected: boolean;
  onSelect: (e: React.SyntheticEvent) => void;
}) {
  const midX = (nodeA.x + nodeB.x) / 2;
  const midY = (nodeA.y + nodeB.y) / 2;
  const strokeWidth = edgeStrokeWidth(edge.similarity);
  const label = formatSimilarity(edge.similarity);

  return (
    <g>
      <line
        x1={nodeA.x}
        y1={nodeA.y}
        x2={nodeB.x}
        y2={nodeB.y}
        stroke="transparent"
        strokeWidth={16}
        className="cursor-pointer"
        role="button"
        tabIndex={0}
        aria-label={`Link between ${nodeA.label} and ${nodeB.label}, similarity ${label}`}
        onClick={onSelect}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") onSelect(e);
        }}
      />
      <line
        x1={nodeA.x}
        y1={nodeA.y}
        x2={nodeB.x}
        y2={nodeB.y}
        strokeWidth={selected ? strokeWidth + 1.5 : strokeWidth}
        className={selected ? "stroke-primary" : "stroke-muted-foreground/60"}
        pointerEvents="none"
      />
      <rect
        x={midX - 15}
        y={midY - 9}
        width={30}
        height={18}
        rx={4}
        className="fill-card stroke-border"
        strokeWidth={1}
        pointerEvents="none"
      />
      <text
        x={midX}
        y={midY}
        textAnchor="middle"
        dominantBaseline="middle"
        className={`select-none text-[10px] font-medium ${selected ? "fill-primary" : "fill-muted-foreground"}`}
        pointerEvents="none"
      >
        {label}
      </text>
    </g>
  );
}
