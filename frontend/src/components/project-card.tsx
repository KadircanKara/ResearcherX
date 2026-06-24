"use client"

import type { CSSProperties } from "react"
import Link from "next/link"
import { Files, MessageSquare, Users } from "lucide-react"
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
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

const MAX_CHIPS = 4

interface ProjectCardProps {
  project: Project
}

export function ProjectCard({ project }: ProjectCardProps) {
  const chips = project.topic_keywords.slice(0, MAX_CHIPS)
  const extra = project.topic_keywords.length - chips.length

  return (
    <Link
      href={`/research/${project.id}`}
      className="group block h-full focus:outline-none"
    >
      <Card
        style={{ "--card-spacing": "1.75rem" } as CSSProperties}
        className="h-full transition-all duration-200 hover:-translate-y-1 hover:shadow-lg focus-within:ring-2 focus-within:ring-ring"
      >
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <CardTitle className="line-clamp-2 text-lg font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
              {project.title}
            </CardTitle>
            <Badge
              variant="outline"
              className="shrink-0 rounded-full text-[11px] font-normal text-muted-foreground"
            >
              {relativeTime(project.updated_at)}
            </Badge>
          </div>
          {project.description && (
            <CardDescription className="mt-1.5 line-clamp-2 leading-relaxed">
              {project.description}
            </CardDescription>
          )}
        </CardHeader>

        {chips.length > 0 && (
          <CardContent>
            <div className="flex flex-wrap gap-1.5">
              {chips.map((k) => (
                <span
                  key={k}
                  className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground"
                >
                  {k}
                </span>
              ))}
              {extra > 0 && (
                <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
                  +{extra} more
                </span>
              )}
            </div>
          </CardContent>
        )}

        <CardFooter className="mt-auto gap-5 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Files className="size-4" />
            {project.counts.papers} papers
          </span>
          <span className="flex items-center gap-1.5">
            <MessageSquare className="size-4" />
            {project.counts.chats} chats
          </span>
          <span className="flex items-center gap-1.5">
            <Users className="size-4" />
            {project.counts.members}
          </span>
        </CardFooter>
      </Card>
    </Link>
  )
}
