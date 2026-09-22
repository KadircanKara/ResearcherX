"use client";

import Link from "next/link";
import { ColorDot } from "@/components/color-dot";
import { InitialsAvatar } from "@/components/initials-avatar";
import { KeywordChips } from "@/components/projects/keyword-chips";
import { ProjectCounts } from "@/components/projects/project-counts";
import { colorFor } from "@/lib/project-colors";
import { projectBlurb } from "@/lib/project-view";
import { routes } from "@/lib/routes";
import type { Member, Project } from "@/lib/types";

/**
 * One project in the list view.
 *
 * `members` arrives separately from the project: the list endpoint carries
 * only a member COUNT, so the page fetches each project's members once the
 * list view is shown. Until they land (or if that fetch fails) the avatar
 * strip is simply empty; the count in `ProjectCounts` is still right.
 */
export function ProjectListRow({ project, members }: { project: Project; members?: Member[] }) {
  return (
    <Link
      href={routes.chat(project.id)}
      className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <ColorDot color={colorFor(project)} />
        <div className="min-w-0">
          <p className="truncate text-[14px] font-medium">{project.title}</p>
          <p className="truncate text-[12px] text-muted-foreground">{projectBlurb(project)}</p>
        </div>
      </div>

      <div className="hidden lg:block">
        <KeywordChips keywords={project.topic_keywords} max={2} />
      </div>

      <ProjectCounts project={project} />

      <ul className="flex -space-x-2">
        {(members ?? []).map((member) => (
          <li key={member.user.id} className="rounded-full ring-2 ring-card">
            <InitialsAvatar person={member.user} size={24} />
          </li>
        ))}
      </ul>
    </Link>
  );
}
