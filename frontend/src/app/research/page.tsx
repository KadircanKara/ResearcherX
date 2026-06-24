"use client"

import { useEffect, useState } from "react"
import { SearchIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ProjectCard } from "@/components/project-card"
import { NewProjectDialog } from "@/components/new-project-dialog"
import { listProjects } from "@/lib/projects"
import { useIdentity } from "@/lib/identity"
import type { Project } from "@/lib/types"

export default function ResearchPage() {
  const { me } = useIdentity()
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState("")

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
    <div className="mx-auto w-full max-w-5xl px-4 py-10">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Research projects
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Organise your research into focused workspaces.
          </p>
        </div>
        <NewProjectDialog />
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
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="h-48 animate-pulse rounded-xl bg-muted"
              />
            ))}
          </div>
        ) : error ? (
          <div className="flex flex-col items-center gap-3 py-20 text-center">
            <p className="text-sm text-muted-foreground">{error}</p>
            <Button variant="outline" size="sm" onClick={load}>
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
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
