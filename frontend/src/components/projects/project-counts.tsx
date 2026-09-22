import { FileText, MessageSquare, Users } from "lucide-react";
import type { Project } from "@/lib/types";

/**
 * The three small counts shown on a project card and list row.
 *
 * Rendered exactly as the list endpoint reports them. Note that the server
 * currently sends `papers` and `chats` as 0 for every project
 * (`project_service.list_projects`); only `members` is counted.
 */
export function ProjectCounts({ project }: { project: Project }) {
  const items = [
    { icon: FileText, value: project.counts.papers, label: "papers" },
    { icon: MessageSquare, value: project.counts.chats, label: "chats" },
    { icon: Users, value: project.counts.members, label: "members" },
  ];
  return (
    <ul className="flex items-center gap-3 text-[12px] text-muted-foreground">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <li key={item.label} className="flex items-center gap-1">
            <Icon className="size-3.5" aria-hidden />
            <span className="tabular-nums">{item.value}</span>
            <span className="sr-only sm:not-sr-only">{item.label}</span>
          </li>
        );
      })}
    </ul>
  );
}
