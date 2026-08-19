"use client"

import { useEffect, useState } from "react"
import { useParams, usePathname } from "next/navigation"
import { ProjectHeader } from "@/components/project-header"
import { ProjectTabs } from "@/components/project-tabs"
import { getProject } from "@/lib/projects"
import { useIdentity } from "@/lib/identity"
import { cn } from "@/lib/utils"
import type { ProjectDetail } from "@/lib/types"

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  const { id } = useParams<{ id: string }>()
  const pathname = usePathname()
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
      <div className="mx-auto w-full max-w-5xl px-6 py-10">
        <div className="h-24 animate-pulse rounded-xl bg-muted" />
        <div className="mt-2 h-10 animate-pulse rounded-xl bg-muted" />
      </div>
    )
  }

  if (notFound || !detail) {
    return (
      <div className="mx-auto w-full max-w-5xl px-6 py-10">
        <div className="flex flex-col items-center gap-3 py-32 text-center">
          <p className="text-base font-medium text-foreground">Project not found</p>
          <p className="text-sm text-muted-foreground">
            This project may have been deleted or you may not have access.
          </p>
        </div>
      </div>
    )
  }

  // The LaTeX tab is a two-pane editor; 5xl is roughly one pane wide. Every
  // other tab is a reading column and stays where it is.
  const wide = pathname.startsWith(`/research/${id}/latex`)

  return (
    <div className={cn("mx-auto w-full px-6 py-8", wide ? "max-w-[110rem]" : "max-w-5xl")}>
      <ProjectHeader detail={detail} />
      <div className="mt-5">
        <ProjectTabs projectId={id} />
      </div>
      <div className="mt-6">{children}</div>
    </div>
  )
}
