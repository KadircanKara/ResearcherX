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

  // Only Chat is a reading column and stays at 5xl. The other three are not,
  // and each states its own cap for its own reason: the LaTeX tab is a
  // three-pane editor and 5xl is roughly one pane wide; the Graph tab is a
  // canvas beside a rail, and the Papers tab a table beside a rail, that have
  // to sit within one eye span, which the concept puts at 2080px (130rem) --
  // past that a row's State cell is a head turn away from the title it
  // belongs to. Papers' own rail column only appears at all above 1240px, so
  // at 5xl (1024px) it could never have rendered.
  const tabWidth = pathname.startsWith(`/research/${id}/latex`)
    ? "max-w-[110rem]"
    : pathname.startsWith(`/research/${id}/graph`) ||
        pathname.startsWith(`/research/${id}/papers`)
      ? "max-w-[130rem]"
      : "max-w-5xl"

  return (
    <div className={cn("mx-auto w-full px-6 py-8", tabWidth)}>
      <ProjectHeader detail={detail} />
      <div className="mt-5">
        <ProjectTabs projectId={id} />
      </div>
      <div className="mt-6">{children}</div>
    </div>
  )
}
