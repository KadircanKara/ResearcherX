"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { FileCode2, Library, MessageSquare, Share2 } from "lucide-react"
import { projectTab, routes, type ProjectTab } from "@/lib/routes"
import { cn } from "@/lib/utils"

const TABS: { key: ProjectTab; label: string; icon: typeof MessageSquare }[] = [
  { key: "chat", label: "Chat", icon: MessageSquare },
  { key: "papers", label: "Papers", icon: Library },
  { key: "graph", label: "Graph", icon: Share2 },
  { key: "latex", label: "LaTeX", icon: FileCode2 },
]

/**
 * The project's section switcher, ported from the prototype: a segmented
 * strip of links. `active` defaults to the tab the current URL is on.
 */
export function ProjectTabs({ projectId, active }: { projectId: string; active?: ProjectTab }) {
  const pathname = usePathname()
  const current = active ?? projectTab(pathname, projectId)

  return (
    <nav
      aria-label="Project sections"
      className="inline-flex w-full max-w-md items-center gap-1 rounded-lg bg-muted p-1 sm:w-auto"
    >
      {TABS.map((tab) => {
        const Icon = tab.icon
        const isActive = tab.key === current
        return (
          <Link
            key={tab.key}
            href={routes[tab.key](projectId)}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              isActive
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="size-3.5" aria-hidden />
            {tab.label}
          </Link>
        )
      })}
    </nav>
  )
}
