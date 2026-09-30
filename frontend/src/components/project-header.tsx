"use client"

import { useEffect, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { colorFor, PROJECT_PALETTE, type ProjectColor } from "@/lib/project-colors"
import { updateProject } from "@/lib/projects"
import { publishProjectColor } from "@/lib/project-store"
import { cn } from "@/lib/utils"
import type { ProjectDetail } from "@/lib/types"

/**
 * The project's own header above the tab strip, ported from the prototype's
 * `project-header.tsx`: eyebrow badge, colour dot + title and description.
 *
 * The colour is applied the moment it is picked -- here and, through
 * `publishProjectColor`, on the sidebar dot -- and reverted if the PATCH
 * fails: keeping a colour the server rejected would be a lie the next reload
 * undoes. Only an owner may change it; anyone else sees a plain dot.
 */
export function ProjectHeader({ detail }: { detail: ProjectDetail }) {
  const { project } = detail
  const canEdit = detail.my_role === "owner"
  const [color, setColor] = useState<ProjectColor>(() => colorFor(project))

  useEffect(() => {
    setColor(colorFor(project))
  }, [project])

  function pick(next: ProjectColor) {
    const previous = color
    setColor(next)
    publishProjectColor({ id: project.id, color: next })
    updateProject(project.id, { color: next }).catch(() => {
      setColor(previous)
      publishProjectColor({ id: project.id, color: previous })
    })
  }

  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <Badge variant="secondary" className="mb-2 text-[10px] uppercase tracking-wide">
          Research project
        </Badge>
        <div className="flex items-center gap-2">
          {canEdit ? (
            <Popover>
              <PopoverTrigger
                render={
                  <button
                    type="button"
                    aria-label="Change project colour"
                    className="size-3 shrink-0 rounded-full ring-offset-2 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    style={{ backgroundColor: color }}
                  />
                }
              />
              <PopoverContent className="w-auto p-2" align="start">
                <p className="mb-2 px-1 text-[11px] font-medium text-muted-foreground">
                  Project colour
                </p>
                <ColorSwatches value={color} onChange={pick} />
              </PopoverContent>
            </Popover>
          ) : (
            <span
              aria-hidden
              className="size-3 shrink-0 rounded-full"
              style={{ backgroundColor: color }}
            />
          )}
          <h1 className="truncate text-lg font-semibold tracking-tight">{project.title}</h1>
        </div>
        {project.description && (
          <p className="mt-1 line-clamp-2 max-w-2xl text-[13px] text-muted-foreground">
            {project.description}
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * The ten project colours as round swatches, the picked one ringed. Shared
 * with the new-project dialog.
 */
export function ColorSwatches({
  value,
  onChange,
}: {
  value: string
  onChange: (color: ProjectColor) => void
}) {
  return (
    <div className="grid grid-cols-5 gap-1.5">
      {PROJECT_PALETTE.map((swatch) => (
        <button
          key={swatch.hex}
          type="button"
          aria-label={swatch.label}
          aria-pressed={value === swatch.hex}
          onClick={() => onChange(swatch.hex)}
          className={cn(
            "size-6 rounded-full ring-offset-2 ring-offset-popover transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            value === swatch.hex && "ring-2 ring-foreground",
          )}
          style={{ backgroundColor: swatch.hex }}
        />
      ))}
    </div>
  )
}
