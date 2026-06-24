"use client"

import { Users } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { ShareDialog } from "@/components/share-dialog"
import type { ProjectDetail } from "@/lib/types"

interface ProjectHeaderProps {
  detail: ProjectDetail
}

export function ProjectHeader({ detail }: ProjectHeaderProps) {
  const { project, members } = detail

  return (
    <div className="border-b border-border bg-background">
      <div className="mx-auto max-w-6xl px-4 py-5">
        <div className="flex items-start justify-between gap-4">
          {/* Left: eyebrow + title + description */}
          <div className="min-w-0 flex-1">
            <Badge variant="secondary" className="mb-2 text-xs font-normal">
              Research project
            </Badge>
            <h1 className="text-xl font-bold tracking-tight text-foreground truncate">
              {project.title}
            </h1>
            {project.description && (
              <p className="mt-1 text-sm text-muted-foreground line-clamp-2">
                {project.description}
              </p>
            )}
          </div>

          {/* Right: member count + share button */}
          <div className="flex shrink-0 items-center gap-3">
            <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <Users className="size-4" />
              {members.length} {members.length === 1 ? "member" : "members"}
            </span>
            <ShareDialog project={project} initialMembers={members} />
          </div>
        </div>
      </div>
    </div>
  )
}
