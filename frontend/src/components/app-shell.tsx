"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Network, Brain, Compass } from "lucide-react";
import { cn } from "@/lib/utils";
import { listProjects } from "@/lib/projects";
import { useIdentity } from "@/lib/identity";
import type { Project } from "@/lib/types";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMenu } from "@/components/user-menu";

const NAV_LINKS = [
  { href: "/research", label: "Research", icon: Brain },
  { href: "/explorer", label: "Explorer", icon: Compass },
];

function initials(name: string): string {
  return name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/** Gradient brand mark — used in the sidebar and the mobile topbar. */
function BrandMark() {
  return (
    <span className="grid size-7 place-items-center rounded-lg gradient-brand text-white shadow-[0_2px_8px_-2px_#3B82F6]">
      <Network className="size-4" />
    </span>
  );
}

function breadcrumbLabel(pathname: string): string {
  if (pathname.startsWith("/research")) return "Research";
  if (pathname.startsWith("/explorer")) return "Explorer";
  return "ResearcherX";
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { me } = useIdentity();
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    let cancelled = false;
    listProjects()
      .then((rows) => {
        if (!cancelled) setProjects(rows);
      })
      .catch(() => {
        if (!cancelled) setProjects([]);
      });
    return () => {
      cancelled = true;
    };
  }, [me?.id]);

  return (
    <div className="flex min-h-screen font-sans">
      {/* Sidebar */}
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground md:flex">
        {/* Brand */}
        <Link
          href="/research"
          className="flex items-center gap-2 px-3 py-3"
        >
          <BrandMark />
          <span className="text-sm font-semibold tracking-tight">ResearcherX</span>
        </Link>

        {/* Primary nav */}
        <nav className="flex flex-col gap-0.5 px-2">
          {NAV_LINKS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
              >
                <Icon className="size-[17px]" />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Projects */}
        <div className="px-2.5 pt-4 pb-1 font-mono text-[11px] font-medium text-muted-foreground">
          Projects
        </div>
        <div className="flex min-h-0 flex-col gap-0.5 overflow-y-auto px-2">
          {projects.length === 0 ? (
            <span className="px-2.5 py-1.5 text-[13px] text-muted-foreground">
              No projects yet
            </span>
          ) : (
            projects.slice(0, 8).map((p) => {
              const href = `/research/${p.id}`;
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={p.id}
                  href={href}
                  title={p.title}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
                    active
                      ? "bg-accent font-medium text-accent-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  )}
                >
                  <span className="size-1.5 shrink-0 rounded-full gradient-brand" />
                  <span className="truncate">{p.title}</span>
                </Link>
              );
            })
          )}
        </div>

        {/* Identity footer */}
        <div className="mt-auto flex items-center gap-2 border-t border-sidebar-border px-2 py-3">
          {me ? (
            <>
              <span
                className={cn(
                  "grid size-7 shrink-0 place-items-center rounded-full text-[11px] font-semibold text-white",
                  !me.avatar_color && "gradient-brand"
                )}
                style={me.avatar_color ? { backgroundColor: me.avatar_color } : undefined}
              >
                {initials(me.name)}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-foreground">
                  {me.name}
                </div>
                <div className="truncate text-[11px] text-muted-foreground">
                  {me.email}
                </div>
              </div>
            </>
          ) : (
            <>
              <span className="size-7 shrink-0 rounded-full bg-muted" />
              <div className="min-w-0 flex-1">
                <div className="h-3 w-20 rounded bg-muted" />
              </div>
            </>
          )}
        </div>
      </aside>

      {/* Content column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Sticky glass topbar */}
        <header className="sticky top-0 z-30 flex h-12 items-center justify-between gap-4 border-b border-border bg-background/80 px-4 backdrop-blur-md">
          <div className="flex items-center gap-2">
            <Link href="/research" className="md:hidden">
              <BrandMark />
            </Link>
            <span className="text-sm text-muted-foreground">
              <b className="font-medium text-foreground">{breadcrumbLabel(pathname)}</b>
            </span>
          </div>

          <div className="flex items-center gap-1">
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>

        <main className="flex-1">{children}</main>
      </div>
    </div>
  );
}
