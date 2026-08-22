"use client"

import Link from "next/link"
import { ChevronRight, FileText, MessageSquare, Users } from "lucide-react"
import { colorFor } from "@/lib/project-colors"
import { relativeTime } from "@/lib/relative-time"
import type { Project } from "@/lib/types"

interface ProjectRowProps {
  project: Project
}

/**
 * One project as a dense row — the list view's counterpart to `ProjectCard`.
 *
 * Deliberately carries less than the card: no keyword chips and a
 * single-line description, because the point of the list is to fit many
 * projects on screen. The colour dot, title, counts and age are the fields
 * both views share, so switching layout never loses information the user was
 * relying on to pick a project.
 */
export function ProjectRow({ project }: ProjectRowProps) {
  return (
    <Link
      href={`/research/${project.id}`}
      className="group flex items-center gap-3 border-b border-border px-3 py-3 transition-colors last:border-b-0 hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <span
        aria-hidden
        className="size-2 shrink-0 rounded-full"
        style={{ backgroundColor: colorFor(project) }}
      />

      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className="truncate text-sm font-medium text-foreground">
          {project.title}
        </span>
        {project.description && (
          <span className="hidden truncate text-[13px] text-muted-foreground sm:block">
            {project.description}
          </span>
        )}
      </div>

      <div className="hidden shrink-0 gap-3.5 text-xs text-muted-foreground sm:flex">
        <span className="flex items-center gap-1.5">
          <FileText className="size-3.5 text-blue-500" />
          {project.counts.papers}
        </span>
        <span className="flex items-center gap-1.5">
          <MessageSquare className="size-3.5 text-emerald-500" />
          {project.counts.chats}
        </span>
        <span className="flex items-center gap-1.5">
          <Users className="size-3.5 text-amber-500" />
          {project.counts.members}
        </span>
      </div>

      <span className="w-24 shrink-0 text-right text-xs text-muted-foreground">
        {relativeTime(project.updated_at)}
      </span>

      <ChevronRight className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </Link>
  )
}
