"use client";

import Link from "next/link";
import { ColorDot } from "@/components/color-dot";
import { KeywordChips } from "@/components/projects/keyword-chips";
import { ProjectCounts } from "@/components/projects/project-counts";
import { relativeLabel } from "@/lib/format";
import { colorFor } from "@/lib/project-colors";
import { projectBlurb } from "@/lib/project-view";
import { routes } from "@/lib/routes";
import type { Project } from "@/lib/types";

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      href={routes.chat(project.id)}
      className="group fade-block flex h-full flex-col rounded-lg border bg-card p-4 transition-colors hover:border-foreground/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <ColorDot color={colorFor(project)} />
          <h2 className="truncate text-[14px] font-semibold">{project.title}</h2>
        </div>
        <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
          updated {relativeLabel(project.updated_at)}
        </span>
      </div>

      <p className="mt-2 line-clamp-2 text-[13px] text-muted-foreground">{projectBlurb(project)}</p>

      <div className="mt-3">
        <KeywordChips keywords={project.topic_keywords} />
      </div>

      <div className="mt-4 flex items-center justify-between border-t pt-3">
        <ProjectCounts project={project} />
        <span className="text-[12px] text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          Open →
        </span>
      </div>
    </Link>
  );
}
