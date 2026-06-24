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
    <div className="border-b border-border bg-background">
      <div className="mx-auto max-w-6xl px-4">
        <nav className="flex items-center gap-1" aria-label="Project tabs">
          {TABS.map(({ slug, label, icon: Icon }) => {
            const href = `/research/${projectId}/${slug}`
            const active = pathname === href || pathname.startsWith(href + "/")
            return (
              <Link
                key={slug}
                href={href}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                  active
                    ? "bg-muted text-primary font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted"
                )}
                aria-current={active ? "page" : undefined}
              >
                <Icon className="size-3.5" />
                {label}
              </Link>
            )
          })}
        </nav>
      </div>
    </div>
  )
}
