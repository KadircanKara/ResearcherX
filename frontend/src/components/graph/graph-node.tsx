import type { GraphNode, GraphPaper } from "@/lib/graph-data";
import { NODE_H, NODE_W } from "@/lib/graph";

/**
 * A node: a rounded card with the paper's short label, centred on the node's
 * x/y. Selected, it gains a primary ring and a red "×" at its top-right corner
 * that takes the paper off the canvas.
 */
export function GraphNodeShape({
  node,
  paper,
  selected,
  onPointerDown,
  onClick,
  onKeyDown,
  onRemove,
}: {
  node: GraphNode;
  paper: GraphPaper | undefined;
  selected: boolean;
  onPointerDown: (e: React.PointerEvent<SVGGElement>) => void;
  onClick: (e: React.MouseEvent) => void;
  onKeyDown: (e: React.KeyboardEvent<SVGGElement>) => void;
  onRemove: () => void;
}) {
  const label = paper ? paper.title : node.label;
  return (
    <g
      transform={`translate(${node.x - NODE_W / 2}, ${node.y - NODE_H / 2})`}
      role="button"
      tabIndex={0}
      aria-label={`${node.label}: ${label}`}
      onPointerDown={onPointerDown}
      onClick={onClick}
      onKeyDown={onKeyDown}
      className="cursor-grab outline-none focus-visible:outline-none active:cursor-grabbing"
    >
      {selected && (
        <rect
          x={-4}
          y={-4}
          width={NODE_W + 8}
          height={NODE_H + 8}
          rx={11}
          className="fill-none stroke-primary"
          strokeWidth={2}
        />
      )}
      <rect
        width={NODE_W}
        height={NODE_H}
        rx={8}
        className="fill-card stroke-border"
        strokeWidth={1.5}
      />
      <text
        x={NODE_W / 2}
        y={NODE_H / 2}
        textAnchor="middle"
        dominantBaseline="middle"
        className="select-none fill-foreground text-[12px] font-medium"
      >
        {node.label}
      </text>
      {selected && (
        <g
          transform={`translate(${NODE_W - 6}, -6)`}
          role="button"
          tabIndex={0}
          aria-label={`Remove ${node.label} from the canvas`}
          className="cursor-pointer outline-none"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              onRemove();
            }
          }}
        >
          <circle r={9} className="fill-destructive" />
          <line
            x1={-4}
            y1={-4}
            x2={4}
            y2={4}
            className="stroke-destructive-foreground"
            strokeWidth={1.5}
          />
          <line
            x1={4}
            y1={-4}
            x2={-4}
            y2={4}
            className="stroke-destructive-foreground"
            strokeWidth={1.5}
          />
        </g>
      )}
    </g>
  );
}
