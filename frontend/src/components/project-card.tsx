"use client"

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

interface ProjectCardProps {
  project: Project
}

export function ProjectCard({ project }: ProjectCardProps) {
  return (
    <Link href={`/research/${project.id}`} className="block group focus:outline-none">
      <Card className="h-full transition-shadow hover:shadow-md focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2">
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="font-semibold leading-snug">
              {project.title}
            </CardTitle>
            <Badge variant="outline" className="shrink-0 text-xs font-normal">
              {relativeTime(project.updated_at)}
            </Badge>
          </div>
          {project.description && (
            <CardDescription className="line-clamp-2">
              {project.description}
            </CardDescription>
          )}
        </CardHeader>

        {project.topic_keywords.length > 0 && (
          <CardContent>
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Topic: </span>
              {project.topic_keywords.join(", ")}
            </p>
          </CardContent>
        )}

        <CardFooter className="mt-auto gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Files className="size-3.5" />
            {project.counts.papers} papers
          </span>
          <span className="flex items-center gap-1">
            <MessageSquare className="size-3.5" />
            {project.counts.chats} chats
          </span>
          <span className="flex items-center gap-1">
            <Users className="size-3.5" />
            {project.counts.members}
          </span>
        </CardFooter>
      </Card>
    </Link>
  )
}
