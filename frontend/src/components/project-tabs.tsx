"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { MessageSquare, FileText, Network, FileCode } from "lucide-react"
import { cn } from "@/lib/utils"

const TABS = [
  { slug: "chat",   label: "Chat",   icon: MessageSquare },
  { slug: "papers", label: "Papers", icon: FileText },
  { slug: "graph",  label: "Graph",  icon: Network },
  { slug: "latex",  label: "LaTeX",  icon: FileCode },
] as const

interface ProjectTabsProps {
  projectId: string
}

export function ProjectTabs({ projectId }: ProjectTabsProps) {
  const pathname = usePathname()

  return (
    <nav
      className="inline-flex items-center gap-0.5 rounded-md bg-muted p-0.5"
      aria-label="Project tabs"
    >
      {TABS.map(({ slug, label, icon: Icon }) => {
        const href = `/research/${projectId}/${slug}`
        const active = pathname === href || pathname.startsWith(href + "/")
        return (
          <Link
            key={slug}
            href={href}
            className={cn(
              "flex items-center gap-1.5 rounded-[5px] px-3 py-1.5 text-sm font-medium transition-colors",
              active
                ? "bg-card text-accent-foreground shadow-sm dark:bg-secondary dark:text-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
            aria-current={active ? "page" : undefined}
          >
            <Icon className="size-3.5" />
            {label}
          </Link>
        )
      })}
    </nav>
  )
}
