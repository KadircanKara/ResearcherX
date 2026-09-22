"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { LayoutGrid, List, Plus } from "lucide-react";
import { NewProjectDialog, type NewProjectValues } from "@/components/projects/new-project-dialog";
import { ProjectCard } from "@/components/projects/project-card";
import { ProjectListRow } from "@/components/projects/project-list-row";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { EmptyState, NoMatchState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { useIdentity } from "@/lib/identity";
import { createProject, listMembers, listProjects } from "@/lib/projects";
import { publishProjectListChanged } from "@/lib/project-store";
import {
  DEFAULT_PROJECT_VIEW,
  PROJECT_VIEW_KEY,
  filterProjects,
  parseProjectView,
  type ProjectView,
} from "@/lib/project-view";
import { routes } from "@/lib/routes";
import type { Member, Project } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The projects list, ported from the prototype's `research.index.tsx`: a top
 * bar with the theme toggle (the shell draws none, and this page sits outside
 * the project layout that draws it for every project page), then the heading,
 * the card/list switch, the search box and the projects.
 */
export function ProjectsPage() {
  const router = useRouter();
  const { me } = useIdentity();
  /** `null` while the first load (or a reload after an identity switch) runs. */
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState(false);
  const [members, setMembers] = useState<Record<string, Member[]>>({});
  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  // Seeded in an effect rather than from a `useState` initializer: the server
  // render has no localStorage, so reading it during the first render would
  // produce markup the client immediately contradicts -- a hydration
  // mismatch. Same trade the sidebar's collapsed state makes in `AppShell`.
  const [view, setView] = useState<ProjectView>(DEFAULT_PROJECT_VIEW);

  // A load started for a previous identity must not land over the current
  // one's, and neither may the member lists it triggered.
  const loadSeq = useRef(0);
  const membersRequested = useRef(new Set<string>());

  useEffect(() => {
    setView(parseProjectView(window.localStorage.getItem(PROJECT_VIEW_KEY)));
  }, []);

  function chooseView(next: ProjectView) {
    setView(next);
    window.localStorage.setItem(PROJECT_VIEW_KEY, next);
  }

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    membersRequested.current = new Set();
    setMembers({});
    setProjects(null);
    setError(false);
    try {
      const rows = await listProjects();
      if (seq === loadSeq.current) setProjects(rows);
    } catch {
      if (seq === loadSeq.current) setError(true);
    }
  }, []);

  // Reload whenever the acting user changes.
  useEffect(() => {
    void load();
  }, [load, me?.id]);

  // The list endpoint carries a member COUNT only; the list view's avatar
  // strip needs the people, so each project's members are fetched once, and
  // only when that view is actually on screen. A failed fetch leaves that
  // row's strip empty rather than failing the page.
  useEffect(() => {
    if (view !== "list" || !projects) return;
    const seq = loadSeq.current;
    for (const project of projects) {
      if (membersRequested.current.has(project.id)) continue;
      membersRequested.current.add(project.id);
      listMembers(project.id)
        .then((rows) => {
          if (seq === loadSeq.current) setMembers((prev) => ({ ...prev, [project.id]: rows }));
        })
        .catch(() => {});
    }
  }, [view, projects]);

  async function handleCreate(values: NewProjectValues) {
    const project = await createProject({
      title: values.title,
      description: values.description || null,
      topic_keywords: values.keywords,
      color: values.color,
    });
    setDialogOpen(false);
    publishProjectListChanged();
    router.push(routes.chat(project.id));
  }

  const filtered = projects ? filterProjects(projects, query) : [];

  return (
    <>
      <div className="hidden h-14 items-center justify-end border-b px-6 lg:flex">
        <ThemeToggle />
      </div>

      <div className="mx-auto w-full max-w-[90rem] space-y-5 px-4 py-6 sm:px-8">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Research projects</h1>
            <p className="mt-1 text-[13px] text-muted-foreground">
              Organise your research into focused workspaces.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center rounded-md border p-0.5">
              <ViewButton
                active={view === "card"}
                label="Card view"
                onClick={() => chooseView("card")}
              >
                <LayoutGrid className="size-4" aria-hidden />
              </ViewButton>
              <ViewButton
                active={view === "list"}
                label="List view"
                onClick={() => chooseView("list")}
              >
                <List className="size-4" aria-hidden />
              </ViewButton>
            </div>
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              <Plus className="size-4" aria-hidden />
              New project
            </Button>
          </div>
        </div>

        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search projects…"
          aria-label="Search projects"
          className="max-w-sm"
        />

        {error ? (
          <EmptyState
            title="Could not load projects"
            body="Check your connection and try again."
          >
            <Button variant="outline" size="sm" onClick={() => void load()}>
              Retry
            </Button>
          </EmptyState>
        ) : projects === null ? (
          <ProjectsSkeleton view={view} />
        ) : projects.length === 0 ? (
          <EmptyState
            title="No projects yet — create one"
            body="Use New project above to make your first workspace."
          />
        ) : filtered.length === 0 ? (
          <NoMatchState query={query} noun="projects" />
        ) : view === "card" ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>
        ) : (
          <div className="fade-block divide-y overflow-hidden rounded-lg border bg-card">
            {filtered.map((project) => (
              <ProjectListRow key={project.id} project={project} members={members[project.id]} />
            ))}
          </div>
        )}
      </div>

      <NewProjectDialog open={dialogOpen} onOpenChange={setDialogOpen} onCreate={handleCreate} />
    </>
  );
}

function ViewButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded-sm p-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/**
 * Placeholders in the shape of the view being loaded. The prototype reads
 * mock data synchronously and has no loading state; these reuse its card and
 * row containers so the page does not jump when the projects land.
 */
function ProjectsSkeleton({ view }: { view: ProjectView }) {
  if (view === "card") {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-busy>
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-40 animate-pulse rounded-lg border bg-card" />
        ))}
      </div>
    );
  }
  return (
    <div className="divide-y overflow-hidden rounded-lg border bg-card" aria-busy>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-center gap-2 px-4 py-3">
          <span className="size-2 shrink-0 rounded-full bg-muted" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="h-3.5 w-48 max-w-full animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-80 max-w-full animate-pulse rounded-md bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
