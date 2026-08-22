"use client"

import { LayoutGrid, List } from "lucide-react"
import { cn } from "@/lib/utils"
import { PROJECT_VIEWS, type ProjectView } from "@/lib/project-view"

const LABELS: Record<ProjectView, { label: string; Icon: typeof LayoutGrid }> = {
  card: { label: "Card view", Icon: LayoutGrid },
  list: { label: "List view", Icon: List },
}

interface ProjectViewToggleProps {
  value: ProjectView
  onChange: (next: ProjectView) => void
}

/**
 * Segmented control for the research page's layout.
 *
 * `radiogroup` rather than two buttons: the two options are one exclusive
 * choice, and the icons are the only label, so the roles and `aria-checked`
 * are what tell a screen reader which layout is active.
 */
export function ProjectViewToggle({ value, onChange }: ProjectViewToggleProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Project layout"
      className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-background p-0.5"
    >
      {PROJECT_VIEWS.map((view) => {
        const { label, Icon } = LABELS[view]
        const active = value === view
        return (
          <button
            key={view}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={label}
            onClick={() => onChange(view)}
            className={cn(
              "grid size-7 place-items-center rounded-md transition-colors",
              active
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            )}
          >
            <Icon className="size-4" />
          </button>
        )
      })}
    </div>
  )
}
