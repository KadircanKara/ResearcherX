import { cn } from "@/lib/utils";

/**
 * A project's colour as a small dot (the sidebar, cards, rows).
 *
 * Takes the colour already resolved -- pass `colorFor(project)` from
 * `lib/project-colors`, never a raw `project.color`, so an unknown string can
 * never reach the `style` attribute.
 */
export function ColorDot({
  color,
  className,
  size = 8,
}: {
  color: string;
  className?: string;
  size?: number;
}) {
  return (
    <span
      aria-hidden
      className={cn("inline-block shrink-0 rounded-full", className)}
      style={{ backgroundColor: color, width: size, height: size }}
    />
  );
}
