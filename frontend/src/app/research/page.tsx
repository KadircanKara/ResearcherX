"use client"

import { useEffect, useState } from "react"
import { SearchIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ProjectCard } from "@/components/project-card"
import { ProjectRow } from "@/components/project-row"
import { ProjectViewToggle } from "@/components/project-view-toggle"
import { NewProjectDialog } from "@/components/new-project-dialog"
import { listProjects } from "@/lib/projects"
import { useIdentity } from "@/lib/identity"
import {
  DEFAULT_PROJECT_VIEW,
  PROJECT_VIEW_KEY,
  parseProjectView,
  type ProjectView,
} from "@/lib/project-view"
import type { Project } from "@/lib/types"

export default function ResearchPage() {
  const { me } = useIdentity()
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  // Seeded in an effect rather than from a `useState` initializer: the server
  // render has no localStorage, so reading it during the first render would
  // produce markup the client immediately contradicts -- a hydration
  // mismatch. Same trade the sidebar's collapsed state makes in `AppShell`.
  const [view, setView] = useState<ProjectView>(DEFAULT_PROJECT_VIEW)

  useEffect(() => {
    setView(parseProjectView(window.localStorage.getItem(PROJECT_VIEW_KEY)))
  }, [])

  function chooseView(next: ProjectView) {
    setView(next)
    window.localStorage.setItem(PROJECT_VIEW_KEY, next)
  }

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const data = await listProjects()
      setProjects(data)
    } catch {
      setError("Could not load projects. Check your connection and try again.")
    } finally {
      setLoading(false)
    }
  }

  // Reload whenever the acting user changes
  useEffect(() => {
    void load()
  }, [me?.id]) // load is defined in component scope; dep on me?.id is intentional

  const filtered = query.trim()
    ? projects.filter((p) => {
        const q = query.toLowerCase()
        return (
          p.title.toLowerCase().includes(q) ||
          p.topic_keywords.some((k) => k.toLowerCase().includes(q))
        )
      })
    : projects

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-12">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-foreground">
            Research projects
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Organise your research into focused workspaces.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ProjectViewToggle value={view} onChange={chooseView} />
          <NewProjectDialog />
        </div>
      </div>

      {/* Search */}
      <div className="relative mt-6">
        <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground pointer-events-none" />
        <Input
          className="pl-8"
          placeholder="Search projects…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {/* Content */}
      <div className="mt-6">
        {loading ? (
          view === "card" ? (
            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div
                  key={i}
                  className="h-56 animate-pulse rounded-xl bg-muted"
                />
              ))}
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-border">
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="h-12 animate-pulse border-b border-border bg-muted last:border-b-0"
                />
              ))}
            </div>
          )
        ) : error ? (
          <div className="flex flex-col items-center gap-3 py-20 text-center">
            <p className="text-sm text-muted-foreground">{error}</p>
            <Button variant="outline" onClick={load}>
              Retry
            </Button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-20 text-center">
            {projects.length === 0 ? (
              <>
                <p className="text-sm font-medium text-foreground">
                  No projects yet — create one
                </p>
                <p className="text-sm text-muted-foreground">
                  Use the <span className="font-medium">New project</span> button above to get started.
                </p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                No projects match &ldquo;{query}&rdquo;
              </p>
            )}
          </div>
        ) : view === "card" ? (
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border">
            {filtered.map((project) => (
              <ProjectRow key={project.id} project={project} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
