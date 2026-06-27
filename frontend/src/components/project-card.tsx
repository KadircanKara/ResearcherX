"use client"

import Link from "next/link"
import { ArrowRight, FileText, MessageSquare, Users } from "lucide-react"
import type { Project } from "@/lib/types"

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const days = Math.floor(diff / 86_400_000)
  if (days === 0) return "Today"
  if (days === 1) return "1 day ago"
  if (days < 30) return `${days} days ago`
  const months = Math.floor(days / 30)
  if (months === 1) return "1 month ago"
  if (months < 12) return `${months} months ago`
  const years = Math.floor(months / 12)
  return years === 1 ? "1 year ago" : `${years} years ago`
}

const MAX_CHIPS = 3

interface ProjectCardProps {
  project: Project
}

export function ProjectCard({ project }: ProjectCardProps) {
  const chips = project.topic_keywords.slice(0, MAX_CHIPS)
  const extra = project.topic_keywords.length - chips.length

  return (
    <Link href={`/research/${project.id}`} className="group block h-full">
      <div className="relative flex h-full flex-col gap-3 overflow-hidden rounded-2xl border border-border bg-card p-6 shadow-sm transition-all duration-300 hover:-translate-y-1.5 hover:shadow-lift hover:border-primary/40 focus-within:ring-2 focus-within:ring-ring">
        <span className="pointer-events-none absolute inset-x-0 top-0 h-[3px] gradient-edge opacity-0 transition-opacity duration-300 group-hover:opacity-100" />

        <div className="flex items-start justify-between gap-3">
          <h3 className="line-clamp-2 text-base font-semibold leading-snug tracking-tight transition-colors group-hover:text-accent-foreground">
            {project.title}
          </h3>
          <span className="shrink-0 rounded-full border border-border px-2 py-0.5 text-[11px] font-normal text-muted-foreground">
            {relativeTime(project.updated_at)}
          </span>
        </div>

        {project.description && (
          <p className="line-clamp-2 text-[13.5px] leading-relaxed text-muted-foreground">
            {project.description}
          </p>
        )}

        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {chips.map((k) => (
              <span
                key={k}
                className="inline-flex items-center rounded-full bg-accent px-2.5 py-0.5 text-[11px] font-medium text-accent-foreground"
              >
                {k}
              </span>
            ))}
            {extra > 0 && (
              <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                +{extra}
              </span>
            )}
          </div>
        )}

        <div className="mt-auto flex items-center justify-between border-t border-border pt-3 text-xs text-muted-foreground">
          <div className="flex gap-3.5">
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
          <span className="inline-flex items-center gap-1 font-medium text-accent-foreground opacity-0 -translate-x-1 transition-all duration-300 group-hover:opacity-100 group-hover:translate-x-0">
            Open <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </span>
        </div>
      </div>
    </Link>
  )
}
