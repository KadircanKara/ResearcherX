"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useParams, usePathname } from "next/navigation"
import { ProjectHeader } from "@/components/project-header"
import { ProjectTabs } from "@/components/project-tabs"
import { ThemeToggle } from "@/components/theme-toggle"
import { getProject } from "@/lib/projects"
import { useIdentity } from "@/lib/identity"
import { projectTab, routes } from "@/lib/routes"
import { cn } from "@/lib/utils"
import type { Member, ProjectDetail } from "@/lib/types"

/**
 * The project shell, ported from the prototype's `research.$id.tsx`: the top
 * bar with the theme toggle, a bordered band holding the project header and
 * its tabs, and the tab's content below. Chat reads as a column (`max-w-4xl`);
 * every other tab takes the full `110rem`.
 */
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

  const onMembersChange = useCallback((members: Member[]) => {
    setDetail((prev) => (prev ? { ...prev, members } : prev))
  }, [])

  const topBar = (
    <div className="hidden h-14 items-center justify-end border-b px-6 lg:flex">
      <ThemeToggle />
    </div>
  )

  if (loading) {
    return (
      <>
        {topBar}
        <div className="border-b">
          <div className="mx-auto w-full max-w-[110rem] space-y-4 px-4 py-5 sm:px-8" aria-busy>
            <div className="space-y-2">
              <div className="h-4 w-28 animate-pulse rounded-md bg-muted" />
              <div className="h-6 w-72 max-w-full animate-pulse rounded-md bg-muted" />
              <div className="h-4 w-96 max-w-full animate-pulse rounded-md bg-muted" />
            </div>
            <div className="h-9 w-full max-w-md animate-pulse rounded-lg bg-muted" />
          </div>
        </div>
      </>
    )
  }

  if (notFound || !detail) {
    return (
      <>
        {topBar}
        <div className="flex min-h-[60vh] items-center justify-center px-4">
          <div className="max-w-md text-center">
            <h1 className="text-xl font-semibold tracking-tight text-foreground">
              Project not found
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              This project may have been deleted or you may not have access.
            </p>
            <div className="mt-6">
              <Link
                href={routes.research()}
                className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
              >
                Back to research
              </Link>
            </div>
          </div>
        </div>
      </>
    )
  }

  const tab = projectTab(pathname, id)
  // Chat reads as a column; every other tab wants the full width.
  const wide = tab !== "chat"

  return (
    <>
      {topBar}
      <div className="border-b">
        <div className="mx-auto w-full max-w-[110rem] space-y-4 px-4 py-5 sm:px-8">
          <ProjectHeader detail={detail} onMembersChange={onMembersChange} />
          <ProjectTabs projectId={id} active={tab} />
        </div>
      </div>
      <div
        className={cn("mx-auto w-full px-4 py-6 sm:px-8", wide ? "max-w-[110rem]" : "max-w-4xl")}
      >
        {children}
      </div>
    </>
  )
}
