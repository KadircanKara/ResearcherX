"use client"

import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import { ProjectHeader } from "@/components/project-header"
import { ProjectTabs } from "@/components/project-tabs"
import { getProject } from "@/lib/projects"
import { useIdentity } from "@/lib/identity"
import type { ProjectDetail } from "@/lib/types"

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  const { id } = useParams<{ id: string }>()
  const { me } = useIdentity()
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setNotFound(false)
    getProject(id)
      .then((d) => {
        setDetail(d)
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err)
        if (msg.includes("404")) {
          setNotFound(true)
        }
      })
      .finally(() => setLoading(false))
  }, [id, me?.id])

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-10">
        <div className="h-24 animate-pulse rounded-xl bg-muted" />
        <div className="mt-2 h-10 animate-pulse rounded-xl bg-muted" />
      </div>
    )
  }

  if (notFound || !detail) {
    return (
      <div className="flex flex-col items-center gap-3 py-32 text-center">
        <p className="text-base font-medium text-foreground">Project not found</p>
        <p className="text-sm text-muted-foreground">
          This project may have been deleted or you may not have access.
        </p>
      </div>
    )
  }

  return (
    <div className="flex min-h-full flex-col">
      <ProjectHeader detail={detail} />
      <ProjectTabs projectId={id} />
      <div className="flex-1">{children}</div>
    </div>
  )
}
